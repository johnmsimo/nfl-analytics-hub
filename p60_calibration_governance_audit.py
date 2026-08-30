"""P6.0 unified calibration governance audit ledger.

P5 established safe, owner-confirmed calibration governance across moneyline,
spread, and total. P6.0 adds a read-only audit boundary over those append-only
registries so operators can verify one ordered history, current champion
consistency, event identity, candidate lineage, and a deterministic portfolio
digest without creating a third mutation path.

The audit performs zero provider calls and zero writes. P5.0 and P5.4 remain the
only promotion/rollback boundaries.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from typing import Any, Iterable

import p50_game_calibration_promotion as p50
import p54_game_market_calibration as p54

MODEL_NAME = "p6.0-calibration-governance-audit-ledger"
MODEL_VERSION = "p60-governance-audit-v1"
MONEYLINE_EVENT_LIMIT = 100
MARKET_EVENT_LIMIT = 200
MARKETS = ("moneyline", "spread", "total")
_FINGERPRINT_RE = re.compile(r"^[0-9a-f]{64}$")


def _timestamp(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _event_digest(event: dict[str, Any]) -> str:
    material = {
        "eventId": event.get("eventId"),
        "market": event.get("market"),
        "action": event.get("action"),
        "candidateId": event.get("candidateId"),
        "family": event.get("family"),
        "parameters": event.get("parameters"),
        "baseModelVersion": event.get("baseModelVersion"),
        "approvedBy": event.get("approvedBy"),
        "governanceFingerprint": event.get("governanceFingerprint"),
        "createdAt": event.get("createdAt"),
        "sourceRegistry": event.get("sourceRegistry"),
    }
    raw = json.dumps(material, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _normalize_moneyline(event: dict[str, Any]) -> dict[str, Any]:
    return {
        "eventId": event.get("eventId"),
        "market": "moneyline",
        "action": event.get("action"),
        "candidateId": event.get("candidateId"),
        "family": event.get("family"),
        "parameters": event.get("parameters") or {},
        "baseModelVersion": event.get("baseModelVersion"),
        "approvedBy": event.get("approvedBy"),
        "governanceFingerprint": event.get("governanceFingerprint"),
        "createdAt": event.get("createdAt"),
        "sourceRegistry": "p5.0-moneyline",
    }


def _normalize_market(event: dict[str, Any]) -> dict[str, Any]:
    return {
        "eventId": event.get("eventId"),
        "market": event.get("market"),
        "action": event.get("action"),
        "candidateId": event.get("candidateId"),
        "family": event.get("family"),
        "parameters": event.get("parameters") or {},
        "baseModelVersion": event.get("baseModelVersion"),
        "approvedBy": event.get("approvedBy"),
        "governanceFingerprint": event.get("governanceFingerprint"),
        "createdAt": event.get("createdAt"),
        "sourceRegistry": "p5.4-spread-total",
    }


def _candidate_lineage_valid(event: dict[str, Any]) -> bool:
    if event.get("action") != "promote":
        return event.get("candidateId") in {None, ""}
    candidate = str(event.get("candidateId") or "")
    market = event.get("market")
    if market == "moneyline":
        return candidate.startswith("p49-")
    if market == "spread":
        return candidate.startswith("p54-sp-")
    if market == "total":
        return candidate.startswith("p54-to-")
    return False


def normalize_events(
    moneyline_events: Iterable[dict[str, Any]],
    market_events: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return one oldest-to-newest public audit stream across all markets."""
    events = [_normalize_moneyline(row) for row in moneyline_events]
    events.extend(_normalize_market(row) for row in market_events)
    events.sort(
        key=lambda row: (
            _timestamp(row.get("createdAt")) is None,
            _timestamp(row.get("createdAt")) or datetime.max,
            str(row.get("eventId") or ""),
        )
    )
    out: list[dict[str, Any]] = []
    for index, row in enumerate(events, start=1):
        event = dict(row)
        event["sequence"] = index
        event["eventDigest"] = _event_digest(event)
        out.append(event)
    return out


def _derive_market_history(events: list[dict[str, Any]], market: str) -> dict[str, Any]:
    state = "baseline"
    candidate_id: str | None = None
    transition_errors: list[str] = []
    market_events = [row for row in events if row.get("market") == market]
    for row in market_events:
        action = row.get("action")
        event_id = str(row.get("eventId") or "unknown")
        if action == "promote":
            candidate_id = str(row.get("candidateId") or "") or None
            state = "promoted"
        elif action == "rollback":
            if state != "promoted":
                transition_errors.append(f"rollback_without_active_champion:{event_id}")
            state = "baseline"
            candidate_id = None
        else:
            transition_errors.append(f"invalid_action:{event_id}")
    return {
        "market": market,
        "eventCount": len(market_events),
        "derivedState": state,
        "derivedCandidateId": candidate_id,
        "latestEventId": market_events[-1].get("eventId") if market_events else None,
        "transitionErrors": transition_errors,
    }


def _champion_consistency(derived: dict[str, Any], champion: dict[str, Any]) -> dict[str, Any]:
    expected_applied = derived.get("derivedState") == "promoted"
    live_applied = champion.get("applied") is True
    expected_candidate = derived.get("derivedCandidateId") if expected_applied else None
    live_candidate = champion.get("candidateId") if live_applied else None
    checks = {
        "registryAvailable": champion.get("available") is not False,
        "appliedStateMatchesHistory": live_applied == expected_applied,
        "candidateMatchesHistory": str(live_candidate or "") == str(expected_candidate or ""),
    }
    return {
        "ok": all(checks.values()),
        "checks": checks,
        "liveState": champion.get("state"),
        "liveCandidateId": live_candidate,
        "expectedState": derived.get("derivedState"),
        "expectedCandidateId": expected_candidate,
    }


def build_audit_report(
    moneyline_events: Iterable[dict[str, Any]],
    market_events: Iterable[dict[str, Any]],
    champions: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Build a deterministic, read-only all-market governance audit report."""
    events = normalize_events(moneyline_events, market_events)
    ids = [str(row.get("eventId") or "") for row in events]
    timestamps = [_timestamp(row.get("createdAt")) for row in events]
    valid_actions = all(row.get("action") in {"promote", "rollback"} for row in events)
    valid_markets = all(row.get("market") in MARKETS for row in events)
    valid_fingerprints = all(
        bool(_FINGERPRINT_RE.fullmatch(str(row.get("governanceFingerprint") or "")))
        for row in events
    )
    valid_timestamps = all(value is not None for value in timestamps)
    unique_event_ids = len(ids) == len(set(ids)) and all(ids)
    lineage_valid = all(_candidate_lineage_valid(row) for row in events)

    market_summaries: dict[str, Any] = {}
    transition_integrity = True
    champion_integrity = True
    for market in MARKETS:
        history = _derive_market_history(events, market)
        consistency = _champion_consistency(history, champions.get(market) or {})
        history["championConsistency"] = consistency
        market_summaries[market] = history
        transition_integrity = transition_integrity and not history["transitionErrors"]
        champion_integrity = champion_integrity and consistency["ok"]

    digest_material = "|".join(row["eventDigest"] for row in events)
    portfolio_digest = hashlib.sha256(digest_material.encode("utf-8")).hexdigest()
    integrity_checks = {
        "eventIdsUnique": unique_event_ids,
        "timestampsValid": valid_timestamps,
        "actionsValid": valid_actions,
        "marketsValid": valid_markets,
        "governanceFingerprintsWellFormed": valid_fingerprints,
        "candidateLineageValid": lineage_valid,
        "stateTransitionsValid": transition_integrity,
        "liveChampionsMatchHistory": champion_integrity,
    }
    ok = all(integrity_checks.values())
    return {
        "available": True,
        "model": MODEL_NAME,
        "modelVersion": MODEL_VERSION,
        "state": "audit-ready" if ok else "audit-degraded",
        "ok": ok,
        "eventCount": len(events),
        "portfolioDigest": portfolio_digest,
        "integrity": {
            "ok": ok,
            "checks": integrity_checks,
            "failedChecks": [key for key, passed in integrity_checks.items() if not passed],
        },
        "markets": market_summaries,
        "events": events,
        "sourceRegistries": {
            "moneyline": "p5.0 game_calibration_promotion_events",
            "spread": "p5.4 game_market_calibration_promotion_events",
            "total": "p5.4 game_market_calibration_promotion_events",
        },
        "safetyContract": {
            "readOnly": True,
            "providerRequests": 0,
            "writesMoneylineRegistry": False,
            "writesMarketRegistry": False,
            "createsMutationEndpoint": False,
            "automaticPromotion": False,
            "automaticRollback": False,
            "changesModelProbabilities": False,
            "changesSelectedSide": False,
            "changesActionabilityThresholds": False,
            "changesBankrollPolicy": False,
            "placesBets": False,
        },
    }


def build_production_report() -> dict[str, Any]:
    """Read both append-only registries and audit them against live champions."""
    moneyline_events = p50.list_events(limit=MONEYLINE_EVENT_LIMIT)
    market_events = p54.list_events(limit=MARKET_EVENT_LIMIT)
    champions = {
        "moneyline": p50.current_champion(),
        "spread": p54.current_champion("spread"),
        "total": p54.current_champion("total"),
    }
    return build_audit_report(moneyline_events, market_events, champions)
