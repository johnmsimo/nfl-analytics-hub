"""P4.7 explicit portfolio confirmation and Tracker integration.

P4.6 produces advisory bankroll allocations. P4.7 lets a user deliberately save
those recommendations into the persistent Tracker without ever placing a bet or
upgrading upstream actionability.

Safety contract:
- only rows already present in the P4.6 ``portfolio`` may be saved;
- every write requires explicit user confirmation;
- confirmation keys fingerprint the exact displayed price/model/allocation row;
- GET/status and dry-run paths are read-only and provider-free;
- repeated confirmations are idempotent through the Tracker's immutable
  first-save key while still allowing stake allocation updates;
- no sportsbook/provider request and no automatic bet placement occurs here.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Iterable

import p46_game_portfolio as p46
import tracker
import value_engine as ve

MODEL_NAME = "p4.7-portfolio-tracker-confirmation"
MODEL_VERSION = "p47-confirmed-tracker-v1"

_MARKET_MAP = {"moneyline": "h2h", "spread": "spread", "total": "total"}
_CONFIRMATION_FIELDS = (
    "gameId",
    "market",
    "selectedSide",
    "selectedTeam",
    "line",
    "bestBook",
    "bestPrice",
    "quoteAt",
    "modelProbability",
    "fairMarketProbability",
    "referenceProbability",
    "edge",
    "evPct",
    "decisionGrade",
    "confidenceScore",
    "recommendedStakePct",
    "recommendedStakeDollars",
    "recommendedStakeUnits",
    "requestedStakePct",
    "requestedStakeDollars",
    "reasons",
    "risks",
)


def _date_from_kickoff(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return datetime.now(timezone.utc).date().isoformat()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text[:10] if len(text) >= 10 else datetime.now(timezone.utc).date().isoformat()
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).date().isoformat()


def _line_token(value: Any) -> str:
    if value is None:
        return ""
    try:
        return f"{float(value):g}"
    except (TypeError, ValueError):
        return str(value)


def _confirmation_snapshot(item: dict[str, Any]) -> dict[str, Any]:
    """Return the material row state a user is actually confirming.

    Deliberately excludes relative-age fields because they change continuously,
    while binding the immutable quote timestamp, price, model outputs and exact
    P4.6 allocation the user saw.
    """
    return {field: item.get(field) for field in _CONFIRMATION_FIELDS}


def tracking_key(item: dict[str, Any]) -> str:
    """Fingerprint one exact displayed P4.6 allocation row.

    If the book, price, model output or recommended stake changes before POST,
    the rebuilt row receives a different key and the stale confirmation fails
    closed as ``unknown_portfolio_selection``.
    """
    raw = json.dumps(
        _confirmation_snapshot(item),
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]
    return ":".join(
        (
            "p47",
            str(item.get("gameId") or ""),
            str(item.get("market") or ""),
            str(item.get("selectedSide") or ""),
            digest,
        )
    )


def _tracker_market(item: dict[str, Any]) -> str:
    return _MARKET_MAP.get(str(item.get("market") or ""), str(item.get("market") or ""))


def _opponent(item: dict[str, Any]) -> str | None:
    selected = item.get("selectedTeam")
    home = item.get("homeTeam")
    away = item.get("awayTeam")
    if selected and selected == home:
        return str(away) if away else None
    if selected and selected == away:
        return str(home) if home else None
    return None


def to_tracker_payload(item: dict[str, Any], report: dict[str, Any]) -> dict[str, Any]:
    """Translate one already-allocated P4.6 row into the existing Tracker contract."""
    if item.get("portfolioEligible") is not True or item.get("actionable") is not True:
        raise ValueError("portfolio row is not eligible for tracking")
    if item.get("opportunityState") != "ACTIONABLE" or item.get("quoteStatus") != "fresh":
        raise ValueError("portfolio row is not a fresh ACTIONABLE market")
    if not item.get("bestBook") or item.get("bestPrice") is None:
        raise ValueError("portfolio row is missing verified sportsbook provenance")
    market_key = _tracker_market(item)
    if market_key not in {"h2h", "spread", "total"}:
        raise ValueError("unsupported game market")
    side = str(item.get("selectedSide") or "").lower()
    if market_key in {"h2h", "spread"} and side not in {"home", "away"}:
        raise ValueError("invalid game side")
    if market_key == "total" and side not in {"over", "under"}:
        raise ValueError("invalid total side")

    kickoff = item.get("kickoffAt")
    price = item.get("bestPrice")
    implied = ve.american_to_implied(price)
    model_probability = item.get("modelProbability")
    selected_team = item.get("selectedTeam")
    return {
        "gameId": item.get("gameId"),
        "season": report.get("season"),
        "week": report.get("week"),
        "gameday": _date_from_kickoff(kickoff),
        "team": selected_team,
        "opponent": _opponent(item),
        "marketKey": market_key,
        "marketLabel": item.get("marketLabel") or str(item.get("market") or "").title(),
        "line": item.get("line"),
        "side": side,
        "price": price,
        "book": item.get("bestBook"),
        "stakeDollars": item.get("recommendedStakeDollars"),
        "stakeUnits": item.get("recommendedStakeUnits"),
        "kellyPct": item.get("kellyPct"),
        "modelProb": model_probability,
        "impliedProb": implied,
        "fairProb": item.get("fairMarketProbability"),
        "fairMarketProb": item.get("fairMarketProbability"),
        "referenceProb": item.get("referenceProbability"),
        "edge": item.get("edge"),
        "evPct": item.get("evPct"),
        "modelSource": p46.MODEL_NAME,
        "decisionModelVersion": p46.MODEL_VERSION,
        "source": MODEL_NAME,
        "confidenceScore": item.get("confidenceScore"),
        "decisionGrade": item.get("decisionGrade"),
        "decisionReasons": list(item.get("reasons") or []),
        "decisionRisks": list(item.get("risks") or []),
        "priceStatus": item.get("priceStatus"),
        "quoteStatus": item.get("quoteStatus"),
        "bestPrice": price,
        "freshBookCount": item.get("freshBookCount"),
        "pairedFairBookCount": item.get("pairedFairBookCount"),
        "oddsSnapshotAgeSeconds": item.get("quoteAgeSeconds"),
        "actionable": True,
    }


def _tracked_keys(store: dict[str, Any]) -> set[tuple[str, str, str, str, str]]:
    keys: set[tuple[str, str, str, str, str]] = set()
    for date, day in store.items():
        if not isinstance(day, dict):
            continue
        for entry in day.get("entries") or []:
            if not isinstance(entry, dict):
                continue
            keys.add(
                (
                    str(date),
                    str(entry.get("gameId") or ""),
                    str(entry.get("marketKey") or ""),
                    _line_token(entry.get("line")),
                    str(entry.get("side") or ""),
                )
            )
    return keys


def _dedup_key(payload: dict[str, Any]) -> tuple[str, str, str, str, str]:
    return (
        str(payload.get("gameday") or ""),
        str(payload.get("gameId") or ""),
        str(payload.get("marketKey") or ""),
        _line_token(payload.get("line")),
        str(payload.get("side") or ""),
    )


def build_tracking_status_from_portfolio(
    report: dict[str, Any],
    *,
    tracked_store: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a read-only view of which P4.6 allocations are already tracked."""
    store = tracker.list_picks() if tracked_store is None else tracked_store
    existing = _tracked_keys(store)
    rows: list[dict[str, Any]] = []
    tracked_count = 0
    for item in report.get("portfolio") or []:
        payload = to_tracker_payload(dict(item), report)
        is_tracked = _dedup_key(payload) in existing
        tracked_count += int(is_tracked)
        rows.append(
            {
                "trackingKey": tracking_key(item),
                "gameId": item.get("gameId"),
                "market": item.get("market"),
                "pickLabel": item.get("pickLabel"),
                "selectedSide": item.get("selectedSide"),
                "line": item.get("line"),
                "book": item.get("bestBook"),
                "price": item.get("bestPrice"),
                "stakeDollars": item.get("recommendedStakeDollars"),
                "stakeUnits": item.get("recommendedStakeUnits"),
                "tracked": is_tracked,
            }
        )
    return {
        "available": bool(report.get("available")),
        "model": MODEL_NAME,
        "modelVersion": MODEL_VERSION,
        "sourcePortfolioModelVersion": report.get("modelVersion"),
        "season": report.get("season"),
        "seasonType": report.get("seasonType"),
        "week": report.get("week"),
        "state": "all-tracked" if rows and tracked_count == len(rows) else "tracking-available" if rows else "nothing-to-track",
        "summary": {
            "portfolioPicks": len(rows),
            "trackedPicks": tracked_count,
            "untrackedPicks": max(0, len(rows) - tracked_count),
        },
        "rows": rows,
        "safety": {
            "providerIo": False,
            "automaticBetPlacement": False,
            "trackerWrite": False,
            "explicitConfirmationRequired": True,
            "confirmationBindsExactAllocation": True,
            "inheritsP46PortfolioEligibility": True,
        },
    }


def build_week_tracking_status(season: int, week: int, season_type: str = "REG") -> dict[str, Any]:
    report = p46.build_week_portfolio(int(season), int(week), str(season_type).upper())
    return build_tracking_status_from_portfolio(report)


def confirm_portfolio_from_report(
    report: dict[str, Any],
    *,
    confirmed: bool,
    selection_keys: Iterable[str] | None = None,
    persist: bool = True,
) -> dict[str, Any]:
    """Persist a confirmed subset of the current P4.6 portfolio to Tracker.

    ``persist=False`` exists for tests/production verification and never writes.
    ``selection_keys=None`` means all current rows; an explicitly empty iterable
    means no rows, never all rows.
    """
    if not confirmed:
        return {
            "ok": False,
            "error": "explicit_confirmation_required",
            "saved": 0,
            "existing": 0,
            "planned": 0,
            "safety": {"providerIo": False, "automaticBetPlacement": False, "trackerWrite": False},
        }

    portfolio = [dict(row) for row in report.get("portfolio") or []]
    by_key = {tracking_key(row): row for row in portfolio}
    selection_source: Iterable[str] = by_key.keys() if selection_keys is None else selection_keys
    requested = list(dict.fromkeys(str(key) for key in selection_source))
    unknown = [key for key in requested if key not in by_key]
    if unknown:
        return {
            "ok": False,
            "error": "unknown_portfolio_selection",
            "unknownSelectionKeys": unknown,
            "saved": 0,
            "existing": 0,
            "planned": 0,
            "safety": {"providerIo": False, "automaticBetPlacement": False, "trackerWrite": False},
        }

    payloads = [to_tracker_payload(by_key[key], report) for key in requested]
    if not persist:
        return {
            "ok": True,
            "mode": "dry-run",
            "saved": 0,
            "existing": 0,
            "planned": len(payloads),
            "selectionKeys": requested,
            "safety": {
                "providerIo": False,
                "automaticBetPlacement": False,
                "trackerWrite": False,
                "explicitUserConfirmation": True,
                "confirmationBindsExactAllocation": True,
            },
        }

    existing_before = _tracked_keys(tracker.list_picks())
    saved = 0
    existing_count = 0
    receipts: list[dict[str, Any]] = []
    for payload in payloads:
        key = _dedup_key(payload)
        was_existing = key in existing_before
        entry = tracker.add_pick(payload)
        receipts.append(
            {
                "id": entry.get("id"),
                "gameId": entry.get("gameId"),
                "marketKey": entry.get("marketKey"),
                "side": entry.get("side"),
                "line": entry.get("line"),
                "stakeDollars": entry.get("stakeDollars"),
                "releaseFingerprint": entry.get("releaseFingerprint"),
                "existing": was_existing,
            }
        )
        existing_count += int(was_existing)
        saved += int(not was_existing)
        existing_before.add(key)

    return {
        "ok": True,
        "mode": "confirmed-tracker-save",
        "saved": saved,
        "existing": existing_count,
        "planned": len(payloads),
        "selectionKeys": requested,
        "receipts": receipts,
        "safety": {
            "providerIo": False,
            "automaticBetPlacement": False,
            "trackerWrite": True,
            "explicitUserConfirmation": True,
            "confirmationBindsExactAllocation": True,
            "sportsbookExecution": False,
        },
    }


def confirm_week_portfolio(
    season: int,
    week: int,
    season_type: str = "REG",
    *,
    confirmed: bool,
    selection_keys: Iterable[str] | None = None,
    persist: bool = True,
) -> dict[str, Any]:
    report = p46.build_week_portfolio(int(season), int(week), str(season_type).upper())
    return confirm_portfolio_from_report(
        report,
        confirmed=confirmed,
        selection_keys=selection_keys,
        persist=persist,
    )
