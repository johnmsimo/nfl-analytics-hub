#!/usr/bin/env python3
"""Run sanitized P4.9 game calibration challenger verification.

Production reads are immutable P4.4 ledger reads. Challenger fitting is performed
in memory. The verifier never refreshes odds, writes Tracker/ledger state, changes
production model parameters, changes bankroll policy, or places a bet.
"""
from __future__ import annotations

import json

from database import db
import p44_game_decision_ledger as p44
import p49_game_calibration as p49


def _receipt(
    idx: int,
    probability: float,
    won: bool,
    *,
    market_probability: float | None,
) -> dict:
    release = {"modelProbability": probability, "marketKey": "moneyline"}
    if market_probability is not None:
        release["fairMarketProbability"] = market_probability
    return {
        "receiptId": f"verify-{idx:04d}",
        "releasedAt": f"{idx:06d}",
        "grade": "win" if won else "loss",
        "release": release,
        "result": {"probability": probability},
    }


def _release_snapshot() -> dict[str, str | None]:
    return {
        str(row.get("receiptId")): row.get("releaseFingerprint")
        for row in p44.list_receipts(limit=2000)
        if row.get("receiptId")
    }


def main() -> int:
    from app import app

    with app.app_context():
        before = p44.ledger_status()
        before_releases = _release_snapshot()
        production = p49.build_production_report()
        after_releases = _release_snapshot()
        after = p44.ledger_status()
        db.session.rollback()

    preserved_releases = all(
        after_releases.get(receipt_id) == fingerprint
        for receipt_id, fingerprint in before_releases.items()
    )
    eligible_receipts = [
        _receipt(i, 0.80, i % 2 == 0, market_probability=0.50)
        for i in range(120)
    ]
    eligible = p49.build_candidate_report(
        eligible_receipts,
        min_samples=80,
        min_validation_samples=24,
        min_market_validation_samples=12,
        train_fraction=0.70,
        min_brier_improvement=0.005,
        max_ece_regression=0.01,
        max_market_skill_regression=0.005,
    )

    regression_receipts = [
        _receipt(i, 0.80, i % 2 == 0, market_probability=0.50)
        for i in range(90)
    ]
    regression_receipts.extend(
        _receipt(90 + idx, 0.80, idx % 2 == 0, market_probability=None)
        for idx in range(18)
    )
    regression_receipts.extend(
        _receipt(108 + idx, 0.80, idx < 10, market_probability=0.80)
        for idx in range(12)
    )
    regression = p49.build_candidate_report(
        regression_receipts,
        min_samples=80,
        min_validation_samples=24,
        min_market_validation_samples=12,
        train_fraction=0.75,
        min_brier_improvement=0.005,
        max_ece_regression=0.20,
        max_market_skill_regression=0.005,
    )

    safety = production.get("safetyContract") or {}
    candidate = eligible.get("candidate") or {}
    regression_candidate = regression.get("candidate") or {}
    gates = {
        "game_ledger_available": before.get("available") is True,
        "production_report_available": production.get("available") is True,
        "production_state_valid": production.get("state")
        in {"collecting", "review", "rejected"},
        "production_is_read_only": safety.get("readOnly") is True,
        "production_is_zero_credit": safety.get("providerRequests") == 0,
        "production_never_auto_applies": production.get("autoApply") is False
        and production.get("productionApplied") is False
        and safety.get("automaticPromotion") is False,
        "production_does_not_change_model": safety.get("changesModelProbabilities")
        is False
        and safety.get("changesActionabilityThresholds") is False,
        "production_does_not_change_bankroll": safety.get("changesBankrollPolicy")
        is False,
        "ledger_release_receipts_preserved": preserved_releases,
        "synthetic_forward_holdout_candidate_eligible": eligible.get("state")
        == "review"
        and (eligible.get("promotionGate") or {}).get("eligible") is True
        and candidate.get("validationIsForwardHoldout") is True,
        "synthetic_brier_improvement_verified": float(
            candidate.get("validationBrierImprovement") or 0.0
        )
        >= 0.005,
        "synthetic_market_skill_guard_verified": (
            eligible.get("promotionGate") or {}
        ).get("checks", {}).get("marketSkillRegressionBounded")
        is True,
        "synthetic_market_regression_rejected": regression.get("state")
        == "rejected"
        and (regression.get("promotionGate") or {}).get("eligible") is False
        and (regression.get("promotionGate") or {})
        .get("checks", {})
        .get("marketSkillRegressionBounded")
        is False
        and float(regression_candidate.get("validationBrierImprovement") or 0.0)
        >= 0.005,
        "synthetic_human_review_required": (
            eligible.get("promotionGate") or {}
        ).get("requiresHumanReview")
        is True
        and (eligible.get("promotionGate") or {}).get("automaticApply") is False,
    }
    failures = [name for name, passed in gates.items() if not passed]
    payload = {
        "phase": "P4.9",
        "mode": "zero-credit-forward-holdout-challenger-verification",
        "ok": not failures,
        "blockingFailures": failures,
        "gates": gates,
        "production": {
            "state": production.get("state"),
            "gradedSamples": production.get("gradedSamples"),
            "candidate": (
                (production.get("candidate") or {}).get("candidateId")
                if production.get("candidate")
                else None
            ),
            "promotionEligible": (
                production.get("promotionGate") or {}
            ).get("eligible"),
        },
        "ledger": {
            "before": before,
            "after": after,
            "releaseReceiptsBefore": len(before_releases),
            "releaseReceiptsAfter": len(after_releases),
        },
    }
    print(json.dumps(payload, sort_keys=True))
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
