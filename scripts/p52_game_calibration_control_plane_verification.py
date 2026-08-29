#!/usr/bin/env python3
"""P5.2 zero-credit, zero-write production control-plane verification."""
from __future__ import annotations

import json

from database import db
import p44_game_decision_ledger as p44
import p50_game_calibration_promotion as p50
import p52_game_calibration_control_plane as p52

CANDIDATE = "p49-p52-verification"


def _challenger(*, state: str, eligible: bool, candidate: bool = True) -> dict:
    return {
        "available": True,
        "state": state,
        "gradedSamples": 120,
        "candidate": {"candidateId": CANDIDATE} if candidate else None,
        "promotionGate": {
            "eligible": eligible,
            "requiresHumanReview": True,
            "automaticApply": False,
        },
    }


def _champion(*, applied: bool, eligible: bool) -> dict:
    return {
        "available": True,
        "champion": {
            "state": "promoted" if applied else "baseline",
            "applied": applied,
            "candidateId": CANDIDATE if applied else None,
        },
        "promotionReview": {"eligible": eligible, "candidateId": CANDIDATE},
    }


def _guard(*, state: str, rollback: bool, samples: int = 0) -> dict:
    return {
        "available": True,
        "state": state,
        "gradedSamples": samples,
        "rollbackGate": {
            "recommended": rollback,
            "requiresHumanReview": True,
            "automaticRollback": False,
        },
    }


def main() -> int:
    from app import app

    with app.app_context():
        receipts_before = p44.list_receipts(limit=2000)
        events_before = p50.list_events(limit=100)
        production = p52.build_production_control_plane()

        promote = p52.build_control_plane(
            _challenger(state="review", eligible=True),
            _champion(applied=False, eligible=True),
            _guard(state="baseline", rollback=False),
        )
        healthy = p52.build_control_plane(
            _challenger(state="rejected", eligible=False, candidate=False),
            _champion(applied=True, eligible=False),
            _guard(state="healthy", rollback=False, samples=30),
        )
        rollback = p52.build_control_plane(
            _challenger(state="review", eligible=True),
            _champion(applied=True, eligible=True),
            _guard(state="rollback-review", rollback=True, samples=30),
        )
        collecting = p52.build_control_plane(
            _challenger(state="collecting", eligible=False, candidate=False),
            _champion(applied=False, eligible=False),
            _guard(state="baseline", rollback=False),
        )

        receipts_after = p44.list_receipts(limit=2000)
        events_after = p50.list_events(limit=100)
        db.session.rollback()

    safety = production.get("safetyContract") or {}
    gates = {
        "production_state_valid": production.get("state")
        in {
            "rollback-review",
            "champion-collecting",
            "champion-healthy",
            "champion-monitor",
            "promotion-review",
            "challenger-collecting",
            "baseline-monitor",
        },
        "production_zero_credit": safety.get("providerRequests") == 0,
        "production_read_only": safety.get("readOnly") is True,
        "automatic_promotion_disabled": safety.get("automaticPromotion") is False,
        "automatic_rollback_disabled": safety.get("automaticRollback") is False,
        "owner_confirmation_required": safety.get("ownerConfirmationRequired") is True,
        "synthetic_promotion_review": promote.get("state") == "promotion-review"
        and promote.get("promoteReady") is True
        and (promote.get("commands") or {}).get("promotion", {}).get("allowed") is True,
        "synthetic_healthy_champion": healthy.get("state") == "champion-healthy"
        and healthy.get("recommendedAction") == "KEEP_CHAMPION",
        "synthetic_rollback_review": rollback.get("state") == "rollback-review"
        and rollback.get("rollbackReady") is True
        and (rollback.get("commands") or {}).get("rollback", {}).get("recommended") is True,
        "synthetic_collecting_is_non_actionable": collecting.get("state") == "challenger-collecting"
        and collecting.get("promoteReady") is False,
        "game_receipts_unchanged": receipts_before == receipts_after,
        "promotion_history_unchanged": events_before == events_after,
    }
    report = {
        "ok": all(gates.values()),
        "phase": "P5.2",
        "mode": "zero-credit-zero-write-calibration-control-plane-verification",
        "gates": gates,
        "blockingFailures": [key for key, passed in gates.items() if not passed],
        "production": {
            "state": production.get("state"),
            "recommendedAction": production.get("recommendedAction"),
            "promoteReady": production.get("promoteReady"),
            "rollbackReady": production.get("rollbackReady"),
            "candidateId": production.get("candidateId"),
            "championCandidateId": production.get("championCandidateId"),
            "receiptCount": len(receipts_before),
            "promotionEventCount": len(events_before),
        },
    }
    print(json.dumps(report, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
