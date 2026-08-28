"""P4.5 smart game-market freshness orchestration.

P4.2 proved that one bounded live hydration can populate the full weekly game
market board. P4.4 later showed why that alone is not enough: a persisted board
can age past the actionability TTL and correctly collapse to no actionable
plays. P4.5 keeps that fail-closed safety contract while adding an economical,
proximity-aware refresh lease for the next upcoming NFL slate.

The scheduler may call :func:`refresh_next_slate` frequently, but provider I/O
only occurs when all of these conditions are true:

* automatic game-market refresh is enabled;
* an upcoming slate exists;
* the first kickoff is inside the protected refresh horizon;
* the persisted P4.2 board is missing or older than the current cadence;
* the caller explicitly allows provider spend.

Product GET routes remain cache-only. This module is an orchestration layer, not
a pricing model, and it never changes P4.1 edge/EV/actionability thresholds.
"""
from __future__ import annotations

from datetime import UTC, datetime
import os
from typing import Any

import nfl_data
import p42_live_market_hydration as p42

MODEL_NAME = "p4.5-smart-market-refresh"
MODEL_VERSION = "p45-refresh-v1"
_TRUE_VALUES = {"1", "true", "yes", "on"}


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
    """Return bounded refresh policy values in minutes/hours."""
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
    """Return the nearest future schedule week across PRE/REG/POST.

    This intentionally does not rely on ``current_week`` because a cached
    current-week marker can remain on the final preseason week after preseason
    has ended while the next real betting slate is REG Week 1.
    """
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
        int(slate["season"]),
        int(slate["week"]),
        str(slate["seasonType"]),
    )
    age = status.get("selectedWeekAgeSeconds")
    cadence = cadence_seconds(float(slate["hoursToFirstKickoff"]))
    if cadence is None:
        state = "standby"
        due = False
    elif not bool(status.get("selectedWeekAvailable")):
        state = "due-missing"
        due = True
    elif not isinstance(age, (int, float)):
        state = "due-unknown-age"
        due = True
    elif float(age) >= float(cadence):
        state = "due-stale"
        due = True
    else:
        state = "fresh-enough"
        due = False

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
