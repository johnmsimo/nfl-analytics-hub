#!/usr/bin/env python3
"""P5.0 zero-credit/zero-write production verification."""
from __future__ import annotations

import json

from database import db
import p50_game_calibration_promotion as p50


def _synthetic_review_report() -> dict:
    return {
        "available": True,
        "model": "p4.9-game-calibration-challenger",
        "modelVersion": "p49-challenger-v1",
        "state": "review",
        "promotionGate": {
            "eligible": True,
            "requiresHumanReview": True,
            "automaticApply": False,
            "checks": {"forwardHoldoutIntegrity": True},
        },
        "candidate": {
            "candidateId": "p49-p50-verification",
            "family": "logit-affine",
            "parameters": {"slope": 0.85, "intercept": 0.02},
            "validationIsForwardHoldout": True,
            "validation": {
                "perMarketChampion": {
                    "moneyline": {"samples": 12, "brier": 0.240, "ece": 0.080},
                },
                "perMarketChallenger": {
                    "moneyline": {"samples": 12, "brier": 0.220, "ece": 0.075},
                },
            },
        },
    }


def main() -> int:
    from app import app

    with app.app_context():
        before = p50.list_events(limit=100)
        status = p50.build_status()
        champion = status.get("champion") or {}

        synthetic = _synthetic_review_report()
        review = p50.assess_candidate_report(synthetic)
        dry_run = p50.promote_candidate(
            synthetic["candidate"]["candidateId"],
            confirmation=p50.PROMOTE_CONFIRMATION,
            actor="p50-production-verifier",
            persist=False,
            report=synthetic,
        )
        rejected_confirmation = p50.promote_candidate(
            synthetic["candidate"]["candidateId"],
            confirmation="NO",
            actor="p50-production-verifier",
            persist=False,
            report=synthetic,
        )
        calibrated = p50.apply_to_selected_probability(
            0.64,
            champion={
                "state": "promoted",
                "applied": True,
                "candidateId": synthetic["candidate"]["candidateId"],
                "family": "logit-affine",
                "parameters": synthetic["candidate"]["parameters"],
                "approvedAt": "synthetic",
            },
        )
        after = p50.list_events(limit=100)
        db.session.rollback()

    gates = {
        "promotion_registry_available": champion.get("available") is True,
        "production_champion_state_valid": champion.get("state") in {"baseline", "promoted"},
        "production_status_zero_credit": (status.get("safetyContract") or {}).get("providerRequests") == 0,
        "automatic_promotion_disabled": (status.get("safetyContract") or {}).get("automaticPromotion") is False,
        "explicit_owner_confirmation_required": (status.get("safetyContract") or {}).get("explicitOwnerConfirmationRequired") is True,
        "append_only_history_declared": (status.get("safetyContract") or {}).get("appendOnlyPromotionHistory") is True,
        "synthetic_moneyline_governance_passes": review.get("eligible") is True,
        "synthetic_dry_run_promotion_passes": dry_run.get("ok") is True and dry_run.get("dryRun") is True,
        "invalid_confirmation_rejected": rejected_confirmation.get("code") == "CONFIRMATION_REQUIRED",
        "synthetic_calibration_applies": calibrated.get("applied") is True and 0.5 <= float(calibrated.get("probability")) <= 0.999,
        "production_history_unchanged": before == after,
    }
    report = {
        "ok": all(gates.values()),
        "phase": "P5.0",
        "mode": "zero-credit-zero-write-controlled-promotion-verification",
        "gates": gates,
        "blockingFailures": [key for key, passed in gates.items() if not passed],
        "production": {
            "championState": champion.get("state"),
            "candidateId": champion.get("candidateId"),
            "promotionApplied": champion.get("applied") is True,
            "eventCount": len(before),
            "challengerState": status.get("challengerState"),
            "gradedSamples": status.get("gradedSamples"),
        },
    }
    print(json.dumps(report, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
