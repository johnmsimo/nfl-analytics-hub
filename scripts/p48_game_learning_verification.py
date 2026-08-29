#!/usr/bin/env python3
"""Run sanitized P4.8 game learning verification in production.

The verifier reads the immutable P4.4 ledger and exercises synthetic calibration
samples only. It performs no odds refresh, Tracker write, ledger mutation, model
parameter update, or sportsbook action.
"""
from __future__ import annotations

import json

from database import db
import p44_game_decision_ledger as p44
import p48_game_learning as p48


def _receipt(
    probability: float,
    grade: str,
    *,
    market_probability: float,
    market: str = "moneyline",
    side: str = "home",
) -> dict:
    return {
        "grade": grade,
        "release": {
            "marketKey": market,
            "decisionGrade": "Play",
            "seasonType": "REG",
            "selectedSide": side,
            "modelProbability": probability,
            "fairMarketProbability": market_probability,
        },
        "result": {
            "probability": probability,
            "unitProfit": 0.9 if grade == "win" else -1.0,
        },
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
        report = p48.build_learning_report()
        after_releases = _release_snapshot()
        after = p44.ledger_status()
        db.session.rollback()

    preserved_releases = all(
        after_releases.get(receipt_id) == fingerprint
        for receipt_id, fingerprint in before_releases.items()
    )
    overconfident = [
        _receipt(
            0.80,
            "win" if idx < 3 else "loss",
            market_probability=0.50,
            market="spread",
        )
        for idx in range(10)
    ]
    review = p48.build_report_from_receipts(
        overconfident,
        min_samples=10,
        min_segment_samples=5,
        calibration_alert=0.05,
        max_ece=0.08,
        market_skill_alert=0.01,
    )
    stable = p48.build_report_from_receipts(
        [
            _receipt(
                0.60,
                "win" if idx < 6 else "loss",
                market_probability=0.50,
                market="total",
            )
            for idx in range(10)
        ],
        min_samples=10,
        min_segment_samples=10,
        calibration_alert=0.05,
        max_ece=0.08,
        market_skill_alert=0.01,
    )

    safety = report.get("safetyContract") or {}
    gates = {
        "game_ledger_available": before.get("available") is True,
        "production_learning_available": report.get("available") is True,
        "production_state_valid": report.get("state") in {"collecting", "review", "stable"},
        "production_is_read_only": safety.get("readOnly") is True,
        "production_is_zero_credit": safety.get("providerRequests") == 0,
        "production_never_auto_applies": report.get("autoApply") is False
        and safety.get("automaticPromotion") is False,
        "production_does_not_change_model": safety.get("changesModelProbabilities") is False
        and safety.get("changesActionabilityThresholds") is False,
        "production_does_not_change_bankroll": safety.get("changesBankrollPolicy") is False,
        "ledger_release_receipts_preserved": preserved_releases,
        "synthetic_overconfidence_detected": any(
            item.get("type") == "overconfidence" for item in review.get("signals") or []
        ),
        "synthetic_negative_market_skill_detected": any(
            item.get("type") == "negative_market_skill" for item in review.get("signals") or []
        ),
        "synthetic_review_requires_human": review.get("state") == "review"
        and (review.get("promotionGate") or {}).get("requiresHumanReview") is True
        and (review.get("promotionGate") or {}).get("automaticApply") is False,
        "synthetic_stable_holds_model": stable.get("state") == "stable"
        and stable.get("recommendedAction") == "hold_game_model"
        and not stable.get("signals"),
    }
    failures = [name for name, passed in gates.items() if not passed]
    payload = {
        "phase": "P4.8",
        "mode": "zero-credit-read-only-game-learning-verification",
        "ok": not failures,
        "blockingFailures": failures,
        "gates": gates,
        "production": {
            "state": report.get("state"),
            "recommendedAction": report.get("recommendedAction"),
            "receiptCount": report.get("receiptCount"),
            "gradedCalibrationSamples": report.get("gradedCalibrationSamples"),
            "signals": len(report.get("signals") or []),
            "marketBenchmarkSamples": (report.get("overall") or {}).get(
                "marketBenchmarkSamples"
            ),
            "brierSkillVsMarket": (report.get("overall") or {}).get(
                "brierSkillVsMarket"
            ),
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
