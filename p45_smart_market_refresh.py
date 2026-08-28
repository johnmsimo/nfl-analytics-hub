"""P4.5 smart game-market refresh and opportunity continuity.

P4.2 proved that one bounded live hydration can populate the full weekly game
market board. P4.4 later showed why that alone is not enough: a persisted board
can age past the actionability TTL and correctly collapse to no actionable
plays. P4.5 keeps that fail-closed safety contract while adding:

* a proximity-aware refresh lease for the next upcoming NFL slate;
* bounded scheduler-driven hydration that only spends when the lease is due;
* a user-facing opportunity layer that keeps strong model ideas visible when a
  sportsbook quote is stale/unpriced without ever upgrading them to actionable.

Product GET routes remain provider-I/O free. Actionability still belongs to
P4.1/P4.2, and only P4.3/P4.4 upstream actionable picks can become bets/ledger
receipts.
"""
from __future__ import annotations

from datetime import UTC, datetime
import os
from typing import Any, Iterable

import market_pricing as mp
import nfl_data
import p42_live_market_hydration as p42

MODEL_NAME = "p4.5-smart-market-refresh"
MODEL_VERSION = "p45-refresh-v1"
_TRUE_VALUES = {"1", "true", "yes", "on"}
_GRADE_RANK = {"Strong Play": 0, "Play": 1, "Lean": 2, "Pass": 3}
_STATE_RANK = {"ACTIONABLE": 0, "WATCH": 1, "REFRESH": 2, "MODEL": 3, "PASS": 4}


def _env_true(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in _TRUE_VALUES


def _env_int(name: str, default: int, low: int, high: int) -> int:
    raw = os.environ.get(name)
    try:
        value = int(raw) if raw not in (None, "") else default
    except (TypeError, ValueError):
        value = default
    return max(low, min(high, value))


def policy() -> dict[str, Any]:
    return {
        "enabled": _env_true("ENABLE_GAME_MARKET_REFRESH", False),
        "refreshHorizonHours": _env_int("P45_REFRESH_HORIZON_HOURS", 168, 24, 336),
        "farMinutes": _env_int("P45_FAR_REFRESH_MINUTES", 120, 30, 360),
        "mediumMinutes": _env_int("P45_MEDIUM_REFRESH_MINUTES", 30, 10, 120),
        "nearMinutes": _env_int("P45_NEAR_REFRESH_MINUTES", 10, 5, 60),
        "imminentMinutes": _env_int("P45_IMMINENT_REFRESH_MINUTES", 5, 5, 30),
        "maxTargetedRequests": _env_int("P45_MAX_TARGETED_REQUESTS", 2, 0, 4),
    }


def _as_utc(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value).strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _game_kickoff(game: dict[str, Any]) -> datetime | None:
    for key in ("date", "commence_time", "kickoff_at", "kickoffAt"):
        parsed = _as_utc(game.get(key))
        if parsed is not None:
            return parsed
    return None


def next_upcoming_slate(
    season: int | None = None,
    *,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    """Return the nearest future schedule week across PRE/REG/POST."""
    current = (now or datetime.now(UTC)).astimezone(UTC)
    target_season = int(season or nfl_data.default_season())
    candidates: list[tuple[datetime, dict[str, Any]]] = []
    for game in nfl_data.get_schedule(target_season):
        if bool(game.get("completed")):
            continue
        kickoff = _game_kickoff(game)
        if kickoff is None or kickoff <= current:
            continue
        stype = str(game.get("season_type") or game.get("type") or "REG").upper()
        if stype not in {"PRE", "REG", "POST"}:
            continue
        candidates.append((kickoff, game))
    if not candidates:
        return None

    first_kickoff, first_game = min(candidates, key=lambda row: row[0])
    stype = str(first_game.get("season_type") or first_game.get("type") or "REG").upper()
    week = int(first_game.get("week") or 0)
    slate_games = [
        game
        for _, game in candidates
        if str(game.get("season_type") or game.get("type") or "REG").upper() == stype
        and int(game.get("week") or 0) == week
    ]
    hours = max(0.0, (first_kickoff - current).total_seconds() / 3600.0)
    return {
        "season": target_season,
        "seasonType": stype,
        "week": week,
        "firstKickoffAt": first_kickoff.isoformat(),
        "hoursToFirstKickoff": round(hours, 3),
        "gameCount": len(slate_games),
    }


def cadence_seconds(hours_to_first_kickoff: float) -> int | None:
    """Return refresh cadence, or ``None`` when the slate is outside horizon."""
    active = policy()
    hours = max(0.0, float(hours_to_first_kickoff))
    if hours > float(active["refreshHorizonHours"]):
        return None
    if hours > 72.0:
        minutes = int(active["farMinutes"])
    elif hours > 24.0:
        minutes = int(active["mediumMinutes"])
    elif hours > 6.0:
        minutes = int(active["nearMinutes"])
    else:
        minutes = int(active["imminentMinutes"])
    return minutes * 60


def refresh_status(
    season: int | None = None,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return a zero-provider-I/O refresh decision for the next slate."""
    active = policy()
    slate = next_upcoming_slate(season, now=now)
    if slate is None:
        return {
            "available": False,
            "model": MODEL_NAME,
            "modelVersion": MODEL_VERSION,
            "state": "no-upcoming-slate",
            "enabled": active["enabled"],
            "due": False,
            "providerSpend": False,
            "slate": None,
            "cache": None,
            "cadenceSeconds": None,
        }

    status = p42.cache_status(
        int(slate["season"]), int(slate["week"]), str(slate["seasonType"])
    )
    age = status.get("selectedWeekAgeSeconds")
    cadence = cadence_seconds(float(slate["hoursToFirstKickoff"]))
    if cadence is None:
        state, due = "standby", False
    elif not bool(status.get("selectedWeekAvailable")):
        state, due = "due-missing", True
    elif not isinstance(age, (int, float)):
        state, due = "due-unknown-age", True
    elif float(age) >= float(cadence):
        state, due = "due-stale", True
    else:
        state, due = "fresh-enough", False

    return {
        "available": True,
        "model": MODEL_NAME,
        "modelVersion": MODEL_VERSION,
        "state": state,
        "enabled": bool(active["enabled"]),
        "due": bool(due),
        "providerSpend": False,
        "slate": slate,
        "cache": status,
        "cadenceSeconds": cadence,
        "policy": active,
    }


def refresh_next_slate(
    season: int | None = None,
    *,
    allow_provider_spend: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Refresh the next slate only when the bounded lease says it is due."""
    status = refresh_status(season, now=now)
    if not status.get("available"):
        return {**status, "ok": True, "providerRequests": 0, "action": "none"}
    if not status.get("enabled"):
        return {
            **status,
            "ok": True,
            "providerRequests": 0,
            "action": "disabled",
            "reason": "ENABLE_GAME_MARKET_REFRESH is false",
        }
    if not status.get("due"):
        return {**status, "ok": True, "providerRequests": 0, "action": "reuse-cache"}
    if not allow_provider_spend:
        return {
            **status,
            "ok": False,
            "providerRequests": 0,
            "action": "blocked",
            "reason": "explicit_provider_spend_permission_required",
        }

    slate = status["slate"]
    hydration = p42.hydrate_week(
        int(slate["season"]),
        int(slate["week"]),
        str(slate["seasonType"]),
        allow_provider_spend=True,
        max_targeted_requests=int(policy()["maxTargetedRequests"]),
    )
    return {
        **status,
        "ok": bool(hydration.get("ok")),
        "state": "refreshed" if hydration.get("ok") else "refresh-failed",
        "providerSpend": True,
        "providerRequests": int(hydration.get("providerRequests") or 0),
        "action": "hydrate",
        "hydration": hydration,
    }


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _blockers(item: dict[str, Any]) -> list[str]:
    if item.get("actionable"):
        return []
    blockers: list[str] = []
    quote = str(item.get("quoteStatus") or "unpriced")
    grade = str(item.get("decisionGrade") or "Pass")
    paired = int(item.get("pairedFairBookCount") or 0)
    edge = _number(item.get("edge"))
    ev = _number(item.get("evPct"))
    if quote != "fresh":
        blockers.append("quote_not_fresh")
    if paired < 1:
        blockers.append("paired_fair_market_missing")
    if grade not in {"Strong Play", "Play"}:
        blockers.append("model_grade_below_actionable")
    if edge is None or edge < mp.MIN_EDGE:
        blockers.append("edge_below_threshold")
    if ev is None or ev < mp.MIN_EV:
        blockers.append("ev_below_threshold")
    return blockers


def _opportunity_state(item: dict[str, Any]) -> str:
    if item.get("actionable"):
        return "ACTIONABLE"
    grade = str(item.get("decisionGrade") or "Pass")
    if grade not in {"Strong Play", "Play", "Lean"}:
        return "PASS"
    quote = str(item.get("quoteStatus") or "unpriced")
    edge = _number(item.get("edge"))
    ev = _number(item.get("evPct"))
    positive_price_signal = (edge is not None and edge > 0) or (ev is not None and ev > 0)
    if quote == "fresh" and positive_price_signal:
        return "WATCH"
    if quote == "stale" and positive_price_signal:
        return "REFRESH"
    return "MODEL"


def _sort_opportunities(items: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        items,
        key=lambda item: (
            _STATE_RANK.get(str(item.get("opportunityState")), 9),
            _GRADE_RANK.get(str(item.get("decisionGrade")), 9),
            -float(item.get("evPct") or -99.0),
            -float(item.get("edge") or -99.0),
            -float(item.get("confidenceScore") or 0.0),
            str(item.get("gameId") or ""),
            str(item.get("market") or ""),
        ),
    )


def enrich_delivery(
    delivery: dict[str, Any],
    *,
    refresh: dict[str, Any] | None = None,
    limit: int = 12,
) -> dict[str, Any]:
    """Add continuity states without changing upstream actionability."""
    all_markets: list[dict[str, Any]] = []
    for source in delivery.get("allMarkets") or []:
        item = dict(source)
        state = _opportunity_state(item)
        item["opportunityState"] = state
        item["actionBlockers"] = _blockers(item)
        if state == "ACTIONABLE":
            item["recommendedAction"] = "BET"
        elif state == "WATCH":
            item["recommendedAction"] = "WATCH PRICE"
        elif state == "REFRESH":
            item["recommendedAction"] = "REFRESH PRICE"
        elif state == "MODEL":
            item["recommendedAction"] = "MODEL LEAN"
        else:
            item["recommendedAction"] = "PASS"
        all_markets.append(item)

    ranked = _sort_opportunities(all_markets)
    opportunities = [item for item in ranked if item["opportunityState"] != "PASS"]
    actionable = [item for item in opportunities if item["opportunityState"] == "ACTIONABLE"]
    watch = [item for item in opportunities if item["opportunityState"] == "WATCH"]
    refresh_needed = [item for item in opportunities if item["opportunityState"] == "REFRESH"]
    model_only = [item for item in opportunities if item["opportunityState"] == "MODEL"]

    if actionable:
        state = "actionable"
        message = f"{len(actionable)} verified game-market opportunities clear every actionability gate."
    elif watch:
        state = "watchlist"
        message = "No verified bet clears every gate; fresh positive-price opportunities remain on watch."
    elif refresh_needed:
        state = "refresh-needed"
        message = "Strong priced opportunities exist, but their quotes must be refreshed before action."
    elif model_only:
        state = "model-opportunities"
        message = "Model opportunities are available; sportsbook pricing is not current enough to act."
    else:
        state = "no-play"
        message = "No current game-market opportunity clears the model-quality floor."

    out = dict(delivery)
    out["model"] = MODEL_NAME
    out["modelVersion"] = MODEL_VERSION
    out["sourceDeliveryModelVersion"] = delivery.get("modelVersion")
    out["state"] = state
    out["message"] = message
    out["opportunities"] = opportunities[: max(1, int(limit))]
    out["allOpportunities"] = ranked
    out["refresh"] = refresh
    summary = dict(delivery.get("summary") or {})
    summary.update(
        {
            "actionableOpportunities": len(actionable),
            "watchOpportunities": len(watch),
            "refreshNeededOpportunities": len(refresh_needed),
            "modelOnlyOpportunities": len(model_only),
            "visibleOpportunities": len(opportunities),
        }
    )
    out["summary"] = summary
    safety = dict(delivery.get("safety") or {})
    safety.update(
        {
            "p45NeverUpgradesActionability": True,
            "providerIoOnProductReads": False,
            "staleQuotesNeverActionable": True,
        }
    )
    out["safety"] = safety
    return out


def build_week_opportunities(
    season: int,
    week: int,
    season_type: str = "REG",
    *,
    limit: int = 12,
) -> dict[str, Any]:
    """Build the P4.5 product contract with zero provider requests.

    P4.4 publication is preserved so any already-actionable P4.3 picks still get
    their immutable first-publication receipt before P4.5 adds UI continuity.
    """
    from p44_game_decision_ledger import publish_week_delivery

    delivery = publish_week_delivery(int(season), int(week), str(season_type).upper(), limit=limit)
    status = refresh_status(int(season))
    return enrich_delivery(delivery, refresh=status, limit=limit)


def verify_opportunity_contract(delivery: dict[str, Any]) -> dict[str, Any]:
    rows = list(delivery.get("allOpportunities") or [])
    actionables = [row for row in rows if row.get("opportunityState") == "ACTIONABLE"]
    gates = {
        "never_upgrades_actionability": all(row.get("actionable") is True for row in actionables),
        "stale_never_actionable": all(
            not row.get("actionable") for row in rows if row.get("quoteStatus") != "fresh"
        ),
        "every_non_actionable_has_blocker": all(
            bool(row.get("actionBlockers")) for row in rows if not row.get("actionable")
        ),
        "visible_opportunities_are_not_passes": all(
            row.get("opportunityState") != "PASS" for row in delivery.get("opportunities") or []
        ),
        "product_reads_are_provider_free": (delivery.get("safety") or {}).get("providerIoOnProductReads") is False,
    }
    return {"ok": all(gates.values()), "gates": gates, "rows": len(rows)}
