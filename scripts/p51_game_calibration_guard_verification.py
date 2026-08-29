#!/usr/bin/env python3
"""P5.1 zero-credit, zero-write production verification."""
from __future__ import annotations

import json

from database import db
import p44_game_decision_ledger as p44
import p49_game_calibration as p49
import p50_game_calibration_promotion as p50
import p51_game_calibration_guard as p51

CANDIDATE = "p49-p51-verification"


def _event(slope: float, intercept: float) -> dict:
    return {
        "eventId": "p50-p51-verification-event",
        "action": "promote",
        "candidateId": CANDIDATE,
        "family": "logit-affine",
        "parameters": {"slope": slope, "intercept": intercept},
        "approvedBy": "p51-verifier",
        "createdAt": "2026-09-01T00:00:00+00:00",
    }


def _champion(slope: float, intercept: float) -> dict:
    return {
        "available": True,
        "state": "promoted",
        "applied": True,
        "candidateId": CANDIDATE,
        "family": "logit-affine",
        "parameters": {"slope": slope, "intercept": intercept},
        "approvedAt": "2026-09-01T00:00:00+00:00",
    }


def _receipts(
    *, baseline_probability: float, slope: float, intercept: float, count: int = 20
) -> list[dict]:
    promoted = p49.calibrate_probability(
        baseline_probability, slope=slope, intercept=intercept
    )
    return [
        {
            "receiptId": f"p51-{idx:03d}",
            "releasedAt": f"2026-09-{(idx % 20) + 1:02d}T00:00:00+00:00",
            "grade": "win" if idx < count // 2 else "loss",
            "release": {
                "marketKey": "moneyline",
                "modelProbability": promoted,
                "fairMarketProbability": 0.55,
                "sourceModelVersion": f"p40-transparent-v1+{CANDIDATE}",
            },
            "result": {"probability": promoted},
        }
        for idx in range(count)
    ]


def main() -> int:
    from app import app

    with app.app_context():
        receipts_before = p44.list_receipts(limit=2000)
        events_before = p50.list_events(limit=100)
        production = p51.build_production_report()

        healthy = p51.build_guard_report(
            _receipts(baseline_probability=0.8, slope=0.5, intercept=0.0),
            [_event(0.5, 0.0)],
            _champion(0.5, 0.0),
        )
        regressed = p51.build_guard_report(
            _receipts(baseline_probability=0.6, slope=1.5, intercept=0.3),
            [_event(1.5, 0.3)],
            _champion(1.5, 0.3),
        )
        collecting = p51.build_guard_report(
            _receipts(
                baseline_probability=0.8,
                slope=0.5,
                intercept=0.0,
                count=10,
            ),
            [_event(0.5, 0.0)],
            _champion(0.5, 0.0),
        )

        receipts_after = p44.list_receipts(limit=2000)
        events_after = p50.list_events(limit=100)
        db.session.rollback()

    safety = production.get("safetyContract") or {}
    gates = {
        "production_state_valid": production.get("state")
        in {"baseline", "collecting", "healthy", "rollback-review"},
        "production_zero_credit": safety.get("providerRequests") == 0,
        "production_read_only": safety.get("readOnly") is True,
        "automatic_rollback_disabled": safety.get("automaticRollback") is False,
        "probability_changes_disabled": safety.get("changesModelProbabilities") is False,
        "healthy_champion_kept": healthy.get("state") == "healthy"
        and healthy.get("recommendation") == "KEEP_PROMOTED_CHAMPION"
        and (healthy.get("rollbackGate") or {}).get("recommended") is False,
        "regressed_champion_requires_human_rollback_review": regressed.get("state")
        == "rollback-review"
        and regressed.get("recommendation") == "REVIEW_ROLLBACK_TO_BASELINE"
        and (regressed.get("rollbackGate") or {}).get("recommended") is True
        and (regressed.get("rollbackGate") or {}).get("automaticRollback") is False,
        "sample_floor_collects_safely": collecting.get("state") == "collecting"
        and (collecting.get("rollbackGate") or {}).get("recommended") is False,
        "game_receipts_unchanged": receipts_before == receipts_after,
        "promotion_history_unchanged": events_before == events_after,
    }
    report = {
        "ok": all(gates.values()),
        "phase": "P5.1",
        "mode": "zero-credit-zero-write-post-promotion-guard-verification",
        "gates": gates,
        "blockingFailures": [key for key, passed in gates.items() if not passed],
        "production": {
            "state": production.get("state"),
            "recommendation": production.get("recommendation"),
            "gradedSamples": production.get("gradedSamples"),
            "candidateId": (production.get("champion") or {}).get("candidateId"),
            "rollbackRecommended": (production.get("rollbackGate") or {}).get("recommended"),
            "receiptCount": len(receipts_before),
            "promotionEventCount": len(events_before),
        },
    }
    print(json.dumps(report, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
