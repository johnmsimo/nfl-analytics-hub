"""P4.6 bankroll-aware game portfolio construction.

P4.5 preserves the complete opportunity set across fresh, stale, and unpriced
states. P4.6 converts only upstream ACTIONABLE game-market opportunities into a
bounded staking plan using the user's tracker bankroll settings.

Safety contract:
- never upgrades WATCH/REFRESH/MODEL/PASS to a bet;
- never invents a sportsbook price, edge, EV, or Kelly value;
- never performs provider I/O;
- never places bets or writes tracker picks automatically;
- caps exposure per bet, per game, and per slate;
- preserves all non-actionable opportunities as zero-stake context.
"""
from __future__ import annotations

import os
from collections import defaultdict
from typing import Any

import p45_smart_market_refresh as p45
import tracker

MODEL_NAME = "p4.6-game-portfolio"
MODEL_VERSION = "p46-bankroll-portfolio-v1"

_GRADE_RANK = {"Strong Play": 0, "Play": 1, "Lean": 2, "Pass": 3}
_MARKET_RANK = {"moneyline": 0, "spread": 1, "total": 2}


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _env_float(name: str, default: float, low: float, high: float) -> float:
    raw = os.environ.get(name)
    try:
        value = float(raw) if raw not in (None, "") else default
    except (TypeError, ValueError):
        value = default
    return _clamp(value, low, high)


def _env_int(name: str, default: int, low: int, high: int) -> int:
    raw = os.environ.get(name)
    try:
        value = int(raw) if raw not in (None, "") else default
    except (TypeError, ValueError):
        value = default
    return max(low, min(high, value))


def policy() -> dict[str, Any]:
    return {
        "maxSlateExposurePct": _env_float("P46_MAX_SLATE_EXPOSURE_PCT", 0.15, 0.01, 0.35),
        "maxGameExposurePct": _env_float("P46_MAX_GAME_EXPOSURE_PCT", 0.075, 0.01, 0.20),
        "minStakePct": _env_float("P46_MIN_STAKE_PCT", 0.0025, 0.0, 0.02),
        "maxPortfolioPicks": _env_int("P46_MAX_PORTFOLIO_PICKS", 8, 1, 20),
        "strongPlayMultiplier": _env_float("P46_STRONG_PLAY_MULTIPLIER", 1.10, 0.75, 1.50),
        "playMultiplier": _env_float("P46_PLAY_MULTIPLIER", 1.00, 0.50, 1.25),
    }


def _number(value: Any, default: float | None = None) -> float | None:
    if isinstance(value, bool):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _priority_score(item: dict[str, Any]) -> float:
    ev = max(0.0, _number(item.get("evPct"), 0.0) or 0.0)
    edge = max(0.0, _number(item.get("edge"), 0.0) or 0.0)
    confidence = _clamp((_number(item.get("confidenceScore"), 0.0) or 0.0) / 100.0, 0.0, 1.0)
    grade_bonus = 1.0 if item.get("decisionGrade") == "Strong Play" else 0.6
    return round(ev * 45.0 + edge * 35.0 + confidence * 15.0 + grade_bonus * 5.0, 4)


def _candidate_sort_key(item: dict[str, Any]) -> tuple[Any, ...]:
    return (
        _GRADE_RANK.get(str(item.get("decisionGrade")), 9),
        -_priority_score(item),
        _MARKET_RANK.get(str(item.get("market")), 9),
        str(item.get("gameId") or ""),
        str(item.get("selectedSide") or ""),
    )


def _settings(raw: dict[str, Any] | None = None) -> dict[str, float]:
    source = dict(raw or tracker.get_settings())
    bankroll = max(0.0, _number(source.get("bankroll"), 1000.0) or 1000.0)
    kelly_fraction = _clamp(_number(source.get("kelly_fraction"), 0.25) or 0.25, 0.0, 1.0)
    max_bet_pct = _clamp(_number(source.get("max_bet_pct"), 0.05) or 0.05, 0.001, 1.0)
    unit_pct = _clamp(_number(source.get("unit_pct"), 0.01) or 0.01, 0.001, 0.10)
    return {
        "bankroll": round(bankroll, 2),
        "kellyFraction": round(kelly_fraction, 4),
        "maxBetPct": round(max_bet_pct, 4),
        "unitPct": round(unit_pct, 4),
    }


def _requested_stake_pct(item: dict[str, Any], settings: dict[str, float], active: dict[str, Any]) -> float:
    full_kelly = max(0.0, _number(item.get("kellyPct"), 0.0) or 0.0)
    grade = str(item.get("decisionGrade") or "Pass")
    multiplier = float(active["strongPlayMultiplier"] if grade == "Strong Play" else active["playMultiplier"])
    requested = full_kelly * float(settings["kellyFraction"]) * multiplier
    return _clamp(requested, 0.0, float(settings["maxBetPct"]))


def _zero_stake_context(item: dict[str, Any]) -> dict[str, Any]:
    row = dict(item)
    row.update({
        "portfolioEligible": False,
        "recommendedStakePct": 0.0,
        "recommendedStakeDollars": 0.0,
        "recommendedStakeUnits": 0.0,
        "portfolioPriorityScore": _priority_score(item),
        "allocationReason": "upstream_not_actionable",
    })
    return row


def build_portfolio_from_opportunities(
    opportunity_board: dict[str, Any],
    *,
    settings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    active = policy()
    user = _settings(settings)
    bankroll = float(user["bankroll"])
    unit_dollars = bankroll * float(user["unitPct"])
    max_slate_dollars = bankroll * float(active["maxSlateExposurePct"])
    max_game_dollars = bankroll * float(active["maxGameExposurePct"])

    all_items = [dict(item) for item in opportunity_board.get("allOpportunities") or []]
    actionable = sorted([
        item for item in all_items
        if item.get("opportunityState") == "ACTIONABLE"
        and item.get("actionable") is True
        and item.get("quoteStatus") == "fresh"
        and item.get("decisionGrade") in {"Strong Play", "Play"}
        and item.get("bestBook")
        and item.get("bestPrice") is not None
        and item.get("fairMarketProbability") is not None
    ], key=_candidate_sort_key)

    slate_remaining = max_slate_dollars
    game_allocated: dict[str, float] = defaultdict(float)
    portfolio: list[dict[str, Any]] = []
    alternates: list[dict[str, Any]] = []

    for item in actionable:
        game_id = str(item.get("gameId") or "")
        requested_pct = _requested_stake_pct(item, user, active)
        requested_dollars = bankroll * requested_pct
        game_remaining = max(0.0, max_game_dollars - game_allocated[game_id])
        allocated = min(requested_dollars, game_remaining, slate_remaining)
        allocated_pct = allocated / bankroll if bankroll > 0 else 0.0

        enriched = dict(item)
        enriched.update({
            "portfolioPriorityScore": _priority_score(item),
            "requestedStakePct": round(requested_pct, 4),
            "requestedStakeDollars": round(requested_dollars, 2),
            "recommendedStakePct": round(allocated_pct, 4),
            "recommendedStakeDollars": round(allocated, 2),
            "recommendedStakeUnits": round(allocated / unit_dollars, 2) if unit_dollars > 0 else 0.0,
            "portfolioEligible": allocated > 0,
            "allocationReason": "allocated",
        })

        if allocated_pct < float(active["minStakePct"]):
            enriched.update({
                "portfolioEligible": False,
                "recommendedStakePct": 0.0,
                "recommendedStakeDollars": 0.0,
                "recommendedStakeUnits": 0.0,
                "allocationReason": "below_minimum_stake_after_caps",
            })
            alternates.append(enriched)
            continue

        if len(portfolio) >= int(active["maxPortfolioPicks"]):
            enriched.update({
                "portfolioEligible": False,
                "recommendedStakePct": 0.0,
                "recommendedStakeDollars": 0.0,
                "recommendedStakeUnits": 0.0,
                "allocationReason": "portfolio_pick_limit",
            })
            alternates.append(enriched)
            continue

        portfolio.append(enriched)
        game_allocated[game_id] += allocated
        slate_remaining = max(0.0, slate_remaining - allocated)

    selected_keys = {(str(row.get("gameId")), str(row.get("market")), str(row.get("selectedSide"))) for row in portfolio}
    alternate_keys = {(str(row.get("gameId")), str(row.get("market")), str(row.get("selectedSide"))) for row in alternates}
    context: list[dict[str, Any]] = []
    for item in all_items:
        key = (str(item.get("gameId")), str(item.get("market")), str(item.get("selectedSide")))
        if key in selected_keys or key in alternate_keys:
            continue
        context.append(_zero_stake_context(item))

    allocated_dollars = round(sum(float(row["recommendedStakeDollars"]) for row in portfolio), 2)
    allocated_pct = round(allocated_dollars / bankroll, 4) if bankroll > 0 else 0.0
    per_game = {game_id: round(amount, 2) for game_id, amount in sorted(game_allocated.items()) if amount > 0}

    if portfolio:
        state = "portfolio-ready"
        message = f"{len(portfolio)} actionable game-market picks fit the protected bankroll portfolio."
    elif actionable:
        state = "allocation-blocked"
        message = "Actionable markets exist, but portfolio exposure limits prevent a recommended stake."
    else:
        state = "no-actionable-portfolio"
        message = "No upstream ACTIONABLE game market currently qualifies for a bankroll stake."

    return {
        "available": bool(opportunity_board.get("available")),
        "model": MODEL_NAME,
        "modelVersion": MODEL_VERSION,
        "sourceOpportunityModelVersion": opportunity_board.get("modelVersion"),
        "season": opportunity_board.get("season"),
        "seasonType": opportunity_board.get("seasonType"),
        "week": opportunity_board.get("week"),
        "state": state,
        "message": message,
        "settings": user,
        "policy": active,
        "summary": {
            "upstreamVisibleOpportunities": int((opportunity_board.get("summary") or {}).get("visibleOpportunities") or 0),
            "upstreamActionableOpportunities": int((opportunity_board.get("summary") or {}).get("actionableOpportunities") or 0),
            "eligibleActionableCandidates": len(actionable),
            "portfolioPicks": len(portfolio),
            "alternateActionableCandidates": len(alternates),
            "allocatedStakeDollars": allocated_dollars,
            "allocatedStakePct": allocated_pct,
            "remainingSlateCapacityDollars": round(max(0.0, max_slate_dollars - allocated_dollars), 2),
            "unitDollars": round(unit_dollars, 2),
            "perGameExposureDollars": per_game,
        },
        "portfolio": portfolio,
        "alternates": alternates,
        "context": context,
        "safety": {
            "cacheOnly": True,
            "providerIo": False,
            "inheritsP45Actionability": True,
            "neverUpgradesActionability": True,
            "nonActionableStakeAlwaysZero": True,
            "automaticBetPlacement": False,
            "perBetCapPct": user["maxBetPct"],
            "perGameCapPct": active["maxGameExposurePct"],
            "slateCapPct": active["maxSlateExposurePct"],
        },
    }


def build_week_portfolio(
    season: int,
    week: int,
    season_type: str = "REG",
    *,
    settings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    opportunities = p45.build_week_opportunities(int(season), int(week), str(season_type).upper())
    return build_portfolio_from_opportunities(opportunities, settings=settings)


def verify_portfolio(report: dict[str, Any]) -> dict[str, Any]:
    portfolio = list(report.get("portfolio") or [])
    alternates = list(report.get("alternates") or [])
    context = list(report.get("context") or [])
    settings = report.get("settings") or {}
    active = report.get("policy") or {}
    bankroll = float(settings.get("bankroll") or 0.0)
    max_slate = bankroll * float(active.get("maxSlateExposurePct") or 0.0)
    max_game = bankroll * float(active.get("maxGameExposurePct") or 0.0)
    per_game: dict[str, float] = defaultdict(float)
    for item in portfolio:
        per_game[str(item.get("gameId") or "")] += float(item.get("recommendedStakeDollars") or 0.0)

    gates = {
        "portfolio_never_upgrades_actionability": all(row.get("actionable") is True and row.get("opportunityState") == "ACTIONABLE" for row in portfolio),
        "portfolio_requires_fresh_verified_price": all(row.get("quoteStatus") == "fresh" and row.get("bestBook") and row.get("bestPrice") is not None and row.get("fairMarketProbability") is not None for row in portfolio),
        "non_actionable_context_has_zero_stake": all(float(row.get("recommendedStakeDollars") or 0.0) == 0.0 for row in context),
        "alternates_have_zero_recommended_stake": all(float(row.get("recommendedStakeDollars") or 0.0) == 0.0 for row in alternates),
        "per_bet_cap_respected": all(float(row.get("recommendedStakePct") or 0.0) <= float(settings.get("maxBetPct") or 0.0) + 1e-9 for row in portfolio),
        "per_game_cap_respected": all(amount <= max_game + 0.01 for amount in per_game.values()),
        "slate_cap_respected": sum(float(row.get("recommendedStakeDollars") or 0.0) for row in portfolio) <= max_slate + 0.01,
        "automatic_betting_disabled": (report.get("safety") or {}).get("automaticBetPlacement") is False,
        "provider_io_disabled": (report.get("safety") or {}).get("providerIo") is False,
    }
    return {
        "ok": all(gates.values()),
        "gates": gates,
        "portfolioPicks": len(portfolio),
        "portfolioStakeDollars": round(sum(float(row.get("recommendedStakeDollars") or 0.0) for row in portfolio), 2),
    }
