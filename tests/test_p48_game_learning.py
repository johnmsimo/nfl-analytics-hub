from __future__ import annotations

import p48_game_learning as p48


def _receipt(
    probability: float,
    grade: str,
    *,
    market_probability: float | None = 0.55,
    market: str = "moneyline",
    decision_grade: str = "Play",
    season_type: str = "REG",
    side: str = "home",
    unit_profit: float | None = None,
):
    release = {
        "marketKey": market,
        "decisionGrade": decision_grade,
        "seasonType": season_type,
        "selectedSide": side,
        "modelProbability": probability,
    }
    if market_probability is not None:
        release["fairMarketProbability"] = market_probability
    return {
        "grade": grade,
        "release": release,
        "result": {
            "probability": probability,
            "unitProfit": unit_profit,
        },
    }


def test_empty_game_learning_report_is_safe_collecting_state():
    report = p48.build_report_from_receipts(
        [], min_samples=10, min_segment_samples=5
    )
    assert report["state"] == "collecting"
    assert report["gradedCalibrationSamples"] == 0
    assert report["autoApply"] is False
    assert report["promotionGate"]["automaticApply"] is False
    assert report["safetyContract"]["providerRequests"] == 0
    assert report["safetyContract"]["changesModelProbabilities"] is False


def test_game_learning_metrics_include_market_benchmark_and_roi():
    report = p48.build_report_from_receipts(
        [
            _receipt(0.80, "win", market_probability=0.60, unit_profit=0.90),
            _receipt(0.80, "loss", market_probability=0.60, unit_profit=-1.0),
        ],
        min_samples=2,
        min_segment_samples=2,
        calibration_alert=0.05,
        max_ece=0.08,
        market_skill_alert=0.01,
    )
    overall = report["overall"]
    assert overall["samples"] == 2
    assert overall["avgProbability"] == 0.8
    assert overall["hitRate"] == 0.5
    assert overall["calibrationGap"] == -0.3
    assert overall["brier"] == 0.34
    assert overall["marketBenchmarkSamples"] == 2
    assert overall["marketBrier"] == 0.26
    assert overall["brierSkillVsMarket"] == -0.08
    assert overall["unitProfit"] == -0.1
    assert overall["unitRoi"] == -0.05


def test_overconfidence_and_negative_market_skill_are_review_only():
    receipts = [
        _receipt(
            0.80,
            "win" if idx < 3 else "loss",
            market_probability=0.50,
            market="spread",
            side="away",
        )
        for idx in range(10)
    ]
    report = p48.build_report_from_receipts(
        receipts,
        min_samples=10,
        min_segment_samples=5,
        calibration_alert=0.05,
        max_ece=0.08,
        market_skill_alert=0.01,
    )
    assert report["state"] == "review"
    assert any(signal["type"] == "overconfidence" for signal in report["signals"])
    assert any(signal["type"] == "negative_market_skill" for signal in report["signals"])
    assert report["promotionGate"]["eligibleForReview"] is True
    assert report["promotionGate"]["requiresHumanReview"] is True
    assert report["promotionGate"]["automaticApply"] is False


def test_market_skill_signal_includes_exact_alert_boundary():
    receipts = [
        _receipt(0.70, "win" if idx < 6 else "loss", market_probability=0.60)
        for idx in range(10)
    ]
    report = p48.build_report_from_receipts(
        receipts,
        min_samples=10,
        min_segment_samples=10,
        calibration_alert=0.25,
        max_ece=0.30,
        market_skill_alert=0.01,
    )
    assert report["overall"]["brierSkillVsMarket"] == -0.01
    assert any(
        signal["type"] == "negative_market_skill"
        and signal["brierSkillVsMarket"] == -0.01
        for signal in report["signals"]
    )
    assert report["state"] == "review"


def test_stable_game_calibration_holds_model_without_auto_apply():
    receipts = [
        _receipt(
            0.60,
            "win" if idx < 6 else "loss",
            market_probability=0.50,
            market="total",
            side="over",
        )
        for idx in range(10)
    ]
    report = p48.build_report_from_receipts(
        receipts,
        min_samples=10,
        min_segment_samples=10,
        calibration_alert=0.05,
        max_ece=0.08,
        market_skill_alert=0.01,
    )
    assert report["state"] == "stable"
    assert report["recommendedAction"] == "hold_game_model"
    assert report["signals"] == []
    assert report["autoApply"] is False


def test_pushes_and_missing_probabilities_are_excluded_from_calibration():
    receipts = [
        _receipt(0.60, "win"),
        _receipt(0.60, "push"),
        {"grade": "loss", "release": {"marketKey": "moneyline"}, "result": {}},
    ]
    report = p48.build_report_from_receipts(
        receipts, min_samples=10, min_segment_samples=5
    )
    assert report["receiptCount"] == 3
    assert report["gradedCalibrationSamples"] == 1


def test_each_game_segment_family_accounts_for_every_calibration_sample():
    receipts = [
        _receipt(0.58, "win", market="moneyline", season_type="REG", side="home"),
        _receipt(0.55, "loss", market="spread", season_type="PRE", side="away"),
        _receipt(0.62, "win", market="total", season_type="POST", side="under"),
    ]
    report = p48.build_report_from_receipts(
        receipts, min_samples=10, min_segment_samples=5
    )
    sample_count = report["overall"]["samples"]
    for family in report["segments"].values():
        assert sum(row["samples"] for row in family.values()) == sample_count


def test_build_learning_report_fails_closed_when_game_ledger_is_unavailable(monkeypatch):
    monkeypatch.setattr(p48.p44, "ledger_status", lambda: {"available": False})
    report = p48.build_learning_report()
    assert report["available"] is False
    assert report["state"] == "unavailable"
    assert report["autoApply"] is False


def test_game_learning_route_exposes_read_only_report(client, monkeypatch):
    monkeypatch.setattr(
        p48,
        "build_learning_report",
        lambda: {
            "available": True,
            "state": "collecting",
            "gradedCalibrationSamples": 0,
            "autoApply": False,
            "safetyContract": {"readOnly": True, "providerRequests": 0},
        },
    )
    response = client.get("/api/game-learning/report")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["state"] == "collecting"
    assert payload["safetyContract"]["providerRequests"] == 0
