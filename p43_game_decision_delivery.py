"""P4.3 user-facing game decision delivery.

P4.2 owns durable live market hydration and P4.1 owns pricing/actionability.
P4.3 does not recalculate either. It converts the persisted game-market board
into a compact decision-first contract for My Hub, the weekly Games page, and
single-game surfaces.

The delivery layer is deliberately cache-only and preserves every upstream
safety decision. A market can only be actionable here when P4.1/P4.2 already
marked it actionable.
"""
from __future__ import annotations

from typing import Any, Iterable

import p42_live_market_hydration as p42

MODEL_NAME = "p4.3-game-decision-delivery"
MODEL_VERSION = "p43-decision-board-v1"

_GRADE_RANK = {"Strong Play": 0, "Play": 1, "Lean": 2, "Pass": 3}
_MARKET_RANK = {"moneyline": 0, "spread": 1, "total": 2}


def _num(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None


def _market_label(row: dict[str, Any], key: str, market: dict[str, Any]) -> str:
    side = str(market.get("selectedSide") or "").lower()
    team = market.get("selectedTeam")
    line = _num(market.get("line"))
    if key == "moneyline":
        return f"{team or side.upper()} ML"
    if key == "spread":
        suffix = ""
        if line is not None:
            suffix = f" {line:+g}"
        return f"{team or side.upper()}{suffix}"
    if key == "total":
        suffix = f" {line:g}" if line is not None else ""
        return f"{side.upper()}{suffix}"
    return str(key).replace("_", " ").title()


def _risk_list(row: dict[str, Any], market: dict[str, Any]) -> list[str]:
    risks = [str(value) for value in (row.get("risks") or []) if value]
    if market.get("risk"):
        risks.append(str(market["risk"]))
    pricing = market.get("pricing") or {}
    if pricing.get("quoteStatus") != "fresh":
        risks.append("Sportsbook quote is not fresh enough for actionability.")
    if int(pricing.get("pairedFairBookCount") or 0) < 1:
        risks.append("No fresh same-book paired market is available for de-vig fair value.")
    return risks[:3]


def _reason_list(row: dict[str, Any], market: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    for item in row.get("reasons") or []:
        if isinstance(item, dict):
            factor = item.get("factor")
            if factor:
                reasons.append(str(factor))
        elif item:
            reasons.append(str(item))
    pricing = market.get("pricing") or {}
    edge = _num(pricing.get("edge"))
    ev = _num(pricing.get("evPct"))
    if edge is not None:
        reasons.append(f"Model edge {edge * 100:.1f}% vs de-vig market")
    if ev is not None:
        reasons.append(f"Expected value {ev * 100:.1f}% at best price")
    return reasons[:3]


def _model_only_moneyline(row: dict[str, Any]) -> dict[str, Any]:
    """Preserve a P4.0 model decision when no sportsbook event is available.

    P4.1 intentionally leaves ``markets`` empty when an event cannot be matched.
    That is correct for pricing, but the delivery layer must not erase the model
    decision completely. This creates an explicitly unpriced, non-actionable
    moneyline view so P4.5 can surface MODEL opportunities without inventing a
    sportsbook price, spread, total, edge, EV, or Kelly value.
    """
    selected_side = str(row.get("selectedSide") or "").lower()
    if selected_side not in {"home", "away"}:
        home_probability = _num(row.get("homeWinProbability"))
        selected_side = "home" if home_probability is None or home_probability >= 0.5 else "away"
    selected_team = row.get("selectedTeam")
    if not selected_team:
        selected_team = row.get("homeTeam") if selected_side == "home" else row.get("awayTeam")
    probability = _num(row.get("selectedProbability"))
    if probability is None:
        probability = _num(
            row.get("homeWinProbability")
            if selected_side == "home"
            else row.get("awayWinProbability")
        )
    probability = probability if probability is not None else 0.5
    return {
        "market": "moneyline",
        "line": None,
        "selectedSide": selected_side,
        "selectedTeam": selected_team,
        "modelProbability": round(probability, 4),
        "confidenceScore": row.get("confidenceScore"),
        "decisionGrade": row.get("decisionGrade") or "Pass",
        "calibration": row.get("calibration"),
        "pricing": {
            "side": selected_side,
            "quoteStatus": "unpriced",
            "priceStatus": "unpriced",
            "bestPrice": None,
            "quotedBookCount": 0,
            "freshBookCount": 0,
            "pairedFairBookCount": 0,
            "fairMarketProbability": None,
            "impliedProbability": None,
            "referenceProbability": None,
            "modelProbability": round(probability, 4),
            "edge": None,
            "evPct": None,
            "kellyPct": None,
            "actionableValue": False,
        },
        "actionable": False,
        "modelOnlyFallback": True,
    }


def flatten_board(board: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten one P4.2 board into UI-ready market decisions."""
    hydrated_at = board.get("hydratedAt")
    hydration_age = board.get("hydrationAgeSeconds")
    items: list[dict[str, Any]] = []
    for row in board.get("rows") or []:
        markets = row.get("markets") if isinstance(row.get("markets"), dict) else {}
        if not markets and row.get("gameId"):
            markets = {"moneyline": _model_only_moneyline(row)}
        calibration = row.get("calibration") if isinstance(row.get("calibration"), dict) else None
        source_model_version = row.get("sourceModel") or row.get("modelVersion")
        for key, market in markets.items():
            if not isinstance(market, dict):
                continue
            pricing = market.get("pricing") if isinstance(market.get("pricing"), dict) else {}
            best = pricing.get("bestPrice") if isinstance(pricing.get("bestPrice"), dict) else None
            market_calibration = market.get("calibration") if isinstance(market.get("calibration"), dict) else calibration
            item = {
                "gameId": row.get("gameId"),
                "season": row.get("season"),
                "seasonType": row.get("seasonType"),
                "week": row.get("week"),
                "kickoffAt": row.get("kickoffAt"),
                "homeTeam": row.get("homeTeam"),
                "awayTeam": row.get("awayTeam"),
                "market": key,
                "marketLabel": str(key).replace("_", " ").title(),
                "pickLabel": _market_label(row, key, market),
                "selectedSide": market.get("selectedSide"),
                "selectedTeam": market.get("selectedTeam"),
                "line": market.get("line"),
                "modelProbability": market.get("modelProbability"),
                "confidenceScore": market.get("confidenceScore"),
                "decisionGrade": market.get("decisionGrade") or "Pass",
                "sourceModelVersion": source_model_version,
                "calibration": market_calibration,
                "quoteStatus": pricing.get("quoteStatus") or "unpriced",
                "priceStatus": pricing.get("priceStatus") or "unpriced",
                "fairMarketProbability": pricing.get("fairMarketProbability"),
                "referenceProbability": pricing.get("referenceProbability"),
                "edge": pricing.get("edge"),
                "evPct": pricing.get("evPct"),
                "kellyPct": pricing.get("kellyPct"),
                "freshBookCount": pricing.get("freshBookCount") or 0,
                "pairedFairBookCount": pricing.get("pairedFairBookCount") or 0,
                "bestBook": best.get("book") if best else None,
                "bestPrice": best.get("price") if best else None,
                "quoteAt": best.get("quoteAt") if best else None,
                "quoteAgeSeconds": best.get("quoteAgeSeconds") if best else None,
                "expiresInSeconds": best.get("expiresInSeconds") if best else None,
                "actionable": bool(market.get("actionable")),
                "modelOnlyFallback": bool(market.get("modelOnlyFallback")),
                "reasons": _reason_list(row, market),
                "risks": _risk_list(row, market),
                "hydratedAt": hydrated_at,
                "hydrationAgeSeconds": hydration_age,
                "href": f"/game/{row.get('gameId')}?season={row.get('season')}" if row.get("gameId") else None,
            }
            items.append(item)
    return items


def _sort(items: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        items,
        key=lambda item: (
            _GRADE_RANK.get(str(item.get("decisionGrade")), 9),
            _MARKET_RANK.get(str(item.get("market")), 9),
            -float(item.get("evPct") or -99.0),
            -float(item.get("edge") or -99.0),
            -float(item.get("confidenceScore") or 0.0),
            str(item.get("gameId") or ""),
        ),
    )


def build_delivery_from_board(board: dict[str, Any], *, limit: int = 12) -> dict[str, Any]:
    """Return the canonical P4.3 decision-first game delivery contract."""
    items = flatten_board(board)
    actionable = _sort(item for item in items if item.get("actionable"))
    watchlist = _sort(
        item
        for item in items
        if not item.get("actionable")
        and item.get("quoteStatus") == "fresh"
        and item.get("decisionGrade") in {"Strong Play", "Play", "Lean"}
    )
    priced = [item for item in items if item.get("quoteStatus") in {"fresh", "stale"}]
    if actionable:
        state = "actionable"
        message = f"{len(actionable)} verified game-market opportunities clear model and price gates."
    elif watchlist:
        state = "watchlist"
        message = "No game market clears every actionability gate; showing the strongest fresh watchlist."
    elif priced:
        state = "priced-no-play"
        message = "Markets are priced, but no Lean-or-better decision currently qualifies."
    else:
        state = "unpriced"
        message = "No persisted sportsbook market is available for this slate."

    market_counts: dict[str, int] = {}
    for item in actionable:
        key = str(item.get("market"))
        market_counts[key] = market_counts.get(key, 0) + 1

    return {
        "available": bool(board.get("available")),
        "model": MODEL_NAME,
        "modelVersion": MODEL_VERSION,
        "sourceModelVersion": board.get("modelVersion"),
        "season": board.get("season"),
        "seasonType": board.get("seasonType"),
        "week": board.get("week"),
        "state": state,
        "message": message,
        "hydrationState": board.get("hydrationState"),
        "hydratedAt": board.get("hydratedAt"),
        "hydrationAgeSeconds": board.get("hydrationAgeSeconds"),
        "summary": {
            "games": int(board.get("gameCount") or 0),
            "pricedGames": int(board.get("pricedGameCount") or 0),
            "freshPricedGames": int(board.get("freshPricedGameCount") or 0),
            "actionableGames": int(board.get("actionableGameCount") or 0),
            "actionableMarkets": len(actionable),
            "watchlistMarkets": len(watchlist),
            "actionableByMarket": dict(sorted(market_counts.items())),
        },
        "picks": actionable[: max(1, int(limit))],
        "watchlist": watchlist[: max(1, int(limit))],
        "allMarkets": _sort(items),
        "safety": {
            "cacheOnly": True,
            "inheritsP41Actionability": True,
            "freshQuoteRequired": True,
            "pairedFairBookRequired": True,
            "noActionabilityRecalculation": True,
        },
    }


def build_week_delivery(season: int, week: int, season_type: str = "REG", *, limit: int = 12) -> dict[str, Any]:
    """Read the P4.2 persisted board and build the P4.3 delivery. No provider I/O."""
    board = p42.build_cached_week_board(int(season), int(week), str(season_type).upper())
    return build_delivery_from_board(board, limit=limit)


def game_delivery(delivery: dict[str, Any], game_id: str) -> dict[str, Any]:
    """Return the P4.3 markets for one game without changing their ordering/safety."""
    game_id = str(game_id)
    rows = [item for item in delivery.get("allMarkets") or [] if str(item.get("gameId")) == game_id]
    picks = [item for item in delivery.get("picks") or [] if str(item.get("gameId")) == game_id]
    priced_rows = [row for row in rows if row.get("quoteStatus") in {"fresh", "stale"}]
    if picks:
        state = "actionable"
    elif priced_rows:
        state = "priced"
    elif rows:
        state = "model-only"
    else:
        state = "unavailable"
    return {
        "gameId": game_id,
        "state": state,
        "picks": picks,
        "markets": rows,
    }


def verify_delivery(delivery: dict[str, Any]) -> dict[str, Any]:
    """Structural P4.3 audit: delivery must never upgrade upstream actionability."""
    all_markets = list(delivery.get("allMarkets") or [])
    picks = list(delivery.get("picks") or [])
    pick_keys = {(p.get("gameId"), p.get("market"), p.get("selectedSide")) for p in picks}
    source_actionable = {
        (p.get("gameId"), p.get("market"), p.get("selectedSide"))
        for p in all_markets
        if p.get("actionable")
    }
    gates = {
        "delivery_does_not_upgrade_actionability": pick_keys <= source_actionable,
        "actionable_picks_are_fresh": all(p.get("quoteStatus") == "fresh" for p in picks),
        "actionable_picks_have_best_price": all(p.get("bestBook") and p.get("bestPrice") is not None for p in picks),
        "actionable_picks_have_fair_market": all(p.get("fairMarketProbability") is not None for p in picks),
        "cache_only_contract": (delivery.get("safety") or {}).get("cacheOnly") is True,
    }
    return {"ok": all(gates.values()), "gates": gates, "pickCount": len(picks), "marketCount": len(all_markets)}
