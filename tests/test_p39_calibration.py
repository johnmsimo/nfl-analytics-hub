from __future__ import annotations

import p39_calibration


def _receipt(idx: int, probability: float, won: bool) -> dict:
    return {
        "receiptId": f"r{idx:04d}",
        "releasedAt": f"2026-09-{1 + idx // 20:02d}T{idx % 20:02d}:00:00+00:00",
        "grade": "win" if won else "loss",
        "release": {"consensusProb": probability},
        "result": {"probability": probability},
    }


def test_collecting_state_before_minimum_samples():
    report = p39_calibration.build_candidate_report(
        [_receipt(i, 0.60, i % 2 == 0) for i in range(20)],
        min_samples=30,
        min_validation_samples=10,
    )
    assert report["state"] == "collecting"
    assert report["candidate"] is None
    assert report["autoApply"] is False
    assert report["productionApplied"] is False
    assert report["promotionGate"]["automaticApply"] is False


def test_overconfident_challenger_improves_forward_holdout_and_is_review_only():
    receipts = []
    for i in range(120):
        # Champion says 80%, reality is a stable 50% on both train and holdout.
        receipts.append(_receipt(i, 0.80, i % 2 == 0))
    report = p39_calibration.build_candidate_report(
        receipts,
        min_samples=80,
        min_validation_samples=24,
        train_fraction=0.70,
        min_brier_improvement=0.005,
        max_ece_regression=0.01,
    )
    candidate = report["candidate"]
    assert candidate is not None
    assert candidate["parameters"]["slope"] < 1.0 or candidate["parameters"]["intercept"] < 0.0
    assert candidate["validationBrierImprovement"] >= 0.005
    assert candidate["validationIsForwardHoldout"] is True
    assert report["state"] == "review"
    assert report["promotionGate"]["eligible"] is True
    assert report["promotionGate"]["requiresHumanReview"] is True
    assert report["promotionGate"]["automaticApply"] is False


def test_identity_like_well_calibrated_data_is_not_promoted():
    receipts = []
    for i in range(120):
        probability = 0.60
        won = (i % 5) < 3  # exactly 60%
        receipts.append(_receipt(i, probability, won))
    report = p39_calibration.build_candidate_report(
        receipts,
        min_samples=80,
        min_validation_samples=24,
        train_fraction=0.70,
        min_brier_improvement=0.005,
        max_ece_regression=0.01,
    )
    assert report["promotionGate"]["eligible"] is False
    assert report["autoApply"] is False
    assert report["productionApplied"] is False


def test_candidate_probability_is_bounded():
    for probability in (0.0, 0.01, 0.50, 0.99, 1.0):
        calibrated = p39_calibration.calibrate_probability(
            probability, slope=1.5, intercept=-0.3
        )
        assert 0.001 <= calibrated <= 0.999


def test_forward_holdout_counts_are_deterministic():
    receipts = [_receipt(i, 0.70, i % 3 != 0) for i in range(100)]
    report = p39_calibration.build_candidate_report(
        receipts,
        min_samples=80,
        min_validation_samples=20,
        train_fraction=0.70,
    )
    candidate = report["candidate"]
    assert candidate is not None
    assert candidate["trainSamples"] == 70
    assert candidate["validationSamples"] == 30
    assert candidate["validationIsForwardHoldout"] is True
