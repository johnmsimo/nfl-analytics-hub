"""Read-only production verification for P3.7 persistent decision history."""
from __future__ import annotations

import hashlib
import json
from typing import Any

import sqlalchemy as sa

from database import db
import decision_ledger
import tracker

_REQUIRED_TABLES = {
    "tracker_day_snapshots",
    "tracker_settings_snapshots",
    "decision_ledger_receipts",
}


def _fingerprint(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _synthetic_contract() -> dict[str, Any]:
    row = {
        "gameId": "p37-game",
        "season": 2026,
        "week": 1,
        "gameday": "2026-09-10",
        "player": "P3.7 Verification Player",
        "playerId": "p37-player",
        "team": "AAA",
        "opponent": "BBB",
        "position": "WR",
        "marketKey": "rec_yds",
        "marketLabel": "Rec Yards",
        "line": 64.5,
        "side": "over",
        "modelMean": 71.2,
        "modelProb": 0.61,
        "consensusProb": 0.63,
        "simulationProb": 0.64,
        "simulationAgreement": 0.9,
        "confidenceScore": 0.78,
        "confidenceGrade": "high",
        "matchupGrade": "favorable",
        "decisionGrade": "Play",
        "decisionScore": 0.67,
        "priceStatus": "positive_value",
        "quoteStatus": "fresh",
        "bestPrice": {"book": "verify-book", "price": -105},
        "edge": 0.04,
        "evPct": 0.06,
        "actionable": True,
        "modelSource": "p3.6-live-market-actionability",
        "decisionModelVersion": "p3.4-simulation-decision",
    }
    context = {
        "source": "p37_read_only_verify",
        "season": 2026,
        "week": 1,
        "season_type": "REG",
        "modelVersion": "p3.7-publication-ledger",
    }
    first = decision_ledger.build_receipt(row, context)
    changed = dict(row)
    changed["bestPrice"] = {"book": "verify-book", "price": 115}
    changed["edge"] = 0.08
    second = decision_ledger.build_receipt(changed, context)
    release = dict(first.get("release") or {})
    return {
        "receiptIdStableAcrossLaterPriceMovement": first.get("receiptId") == second.get("receiptId"),
        "releaseKeyStableAcrossLaterPriceMovement": first.get("releaseKey") == second.get("releaseKey"),
        "fingerprintChangesWhenReleasePayloadChanges": first.get("releaseFingerprint") != second.get("releaseFingerprint"),
        "fingerprintValid": first.get("releaseFingerprint") == _fingerprint(release),
        "receiptIdLength": len(str(first.get("receiptId") or "")),
        "fingerprintLength": len(str(first.get("releaseFingerprint") or "")),
        "decisionGrade": release.get("decisionGrade"),
        "actionable": bool(release.get("actionable")),
        "price": release.get("price"),
        "book": release.get("book"),
    }


def readiness_snapshot() -> dict[str, Any]:
    inspector = sa.inspect(db.engine)
    table_names = set(inspector.get_table_names())
    tracker_status = tracker.persistence_status()
    ledger_status = decision_ledger.ledger_status()
    ledger_performance = decision_ledger.performance_summary()
    receipts = decision_ledger.list_receipts(limit=100)
    synthetic = _synthetic_contract()

    receipt_fingerprints_valid = True
    immutable_release_fields_present = True
    for receipt in receipts:
        release = dict(receipt.get("release") or {})
        expected = _fingerprint(release)
        if receipt.get("releaseFingerprint") != expected:
            receipt_fingerprints_valid = False
        for key in ("gameId", "marketKey", "side", "decisionGrade"):
            if release.get(key) in (None, ""):
                immutable_release_fields_present = False

    brier = ledger_performance.get("brier")
    ece = ledger_performance.get("ece")
    calibration_bounded = (
        (brier is None or isinstance(brier, (int, float)) and 0.0 <= float(brier) <= 1.0)
        and (ece is None or isinstance(ece, (int, float)) and 0.0 <= float(ece) <= 1.0)
    )
    gates = {
        "required_tables_present": _REQUIRED_TABLES.issubset(table_names),
        "tracker_database_persistence": (
            tracker_status.get("backend") == "database" and tracker_status.get("available") is True
        ),
        "ledger_database_persistence": (
            ledger_status.get("backend") == "database" and ledger_status.get("available") is True
        ),
        "synthetic_receipt_identity": (
            synthetic["receiptIdStableAcrossLaterPriceMovement"]
            and synthetic["releaseKeyStableAcrossLaterPriceMovement"]
            and synthetic["receiptIdLength"] == 20
        ),
        "synthetic_release_integrity": (
            synthetic["fingerprintChangesWhenReleasePayloadChanges"]
            and synthetic["fingerprintValid"]
            and synthetic["fingerprintLength"] == 64
            and synthetic["decisionGrade"] == "Play"
            and synthetic["actionable"] is True
            and synthetic["price"] == -105
            and synthetic["book"] == "verify-book"
        ),
        "persisted_receipt_fingerprints": receipt_fingerprints_valid,
        "persisted_release_fields": immutable_release_fields_present,
        "calibration_metrics_bounded": calibration_bounded,
    }
    return {
        "phase": "P3.7",
        "mode": "read-only",
        "ok": all(gates.values()),
        "gates": gates,
        "trackerPersistence": tracker_status,
        "publicationLedger": ledger_status,
        "ledgerPerformance": ledger_performance,
        "sampledReceipts": len(receipts),
        "syntheticReceiptContract": synthetic,
        "requiredTables": sorted(_REQUIRED_TABLES),
    }
