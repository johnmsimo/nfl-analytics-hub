from __future__ import annotations

import p38_learning


def _receipt(
    probability: float,
    grade: str,
    *,
    market: str = "pass_yds",
    decision_grade: str = "Play",
    confidence_grade: str = "A",
    side: str = "over",
    unit_profit: float | None = None,
):
    return {
        "grade": grade,
        "release": {
            "marketKey": market,
            "decisionGrade": decision_grade,
            "confidenceGrade": confidence_grade,
            "side": side,
            "consensusProb": probability,
        },
        "result": {
            "probability": probability,
            "unitProfit": unit_profit,
        },
    }


def test_empty_learning_report_is_safe_collecting_state():
    report = p38_learning.build_report_from_receipts(
        [], min_samples=10, min_segment_samples=5
    )
    assert report["state"] == "collecting"
    assert report["gradedCalibrationSamples"] == 0
    assert report["autoApply"] is False
    assert report["promotionGate"]["automaticApply"] is False


def test_learning_metrics_use_immutable_release_probability_and_outcome():
    report = p38_learning.build_report_from_receipts(
        [
            _receipt(0.80, "win", unit_profit=0.90),
            _receipt(0.80, "loss", unit_profit=-1.0),
        ],
        min_samples=2,
        min_segment_samples=2,
        calibration_alert=0.05,
        max_ece=0.08,
    )
    overall = report["overall"]
    assert overall["samples"] == 2
    assert overall["avgProbability"] == 0.8
    assert overall["hitRate"] == 0.5
    assert overall["calibrationGap"] == -0.3
    assert overall["brier"] == 0.34
    assert overall["pricedSamples"] == 2
    assert overall["unitRoi"] == -0.05


def test_overconfidence_signal_is_segmented_and_review_only():
    receipts = [
        _receipt(0.80, "win" if idx < 3 else "loss", market="rec_yds")
        for idx in range(10)
    ]
    report = p38_learning.build_report_from_receipts(
        receipts,
        min_samples=10,
        min_segment_samples=5,
        calibration_alert=0.05,
        max_ece=0.08,
    )
    assert report["state"] == "review"
    assert any(
        signal["type"] == "overconfidence"
        and signal["scope"] in {"overall", "perMarket"}
        for signal in report["signals"]
    )
    assert report["promotionGate"]["eligibleForReview"] is True
    assert report["promotionGate"]["requiresHumanReview"] is True
    assert report["promotionGate"]["automaticApply"] is False


def test_stable_calibration_holds_model_without_auto_apply():
    receipts = [
        _receipt(0.60, "win" if idx < 6 else "loss")
        for idx in range(10)
    ]
    report = p38_learning.build_report_from_receipts(
        receipts,
        min_samples=10,
        min_segment_samples=10,
        calibration_alert=0.05,
        max_ece=0.08,
    )
    assert report["state"] == "stable"
    assert report["recommendedAction"] == "hold_model"
    assert report["signals"] == []
    assert report["autoApply"] is False


def test_each_segment_family_accounts_for_every_calibration_sample():
    receipts = [
        _receipt(0.58, "win", market="rush_yds", confidence_grade="B", side="over"),
        _receipt(0.55, "loss", market="rec_yds", confidence_grade="C", side="under"),
        _receipt(0.62, "win", market="pass_yds", confidence_grade="A", side="over"),
    ]
    report = p38_learning.build_report_from_receipts(
        receipts, min_samples=10, min_segment_samples=5
    )
    sample_count = report["overall"]["samples"]
    for family in report["segments"].values():
        assert sum(row["samples"] for row in family.values()) == sample_count
