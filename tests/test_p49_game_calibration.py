from __future__ import annotations

import p49_game_calibration as p49


def _receipt(
    idx: int,
    probability: float,
    won: bool,
    *,
    market_probability: float | None = 0.50,
    market: str = "moneyline",
) -> dict:
    release = {
        "modelProbability": probability,
        "marketKey": market,
    }
    if market_probability is not None:
        release["fairMarketProbability"] = market_probability
    return {
        "receiptId": f"g{idx:04d}",
        "releasedAt": f"{idx:06d}",
        "grade": "win" if won else "loss",
        "release": release,
        "result": {"probability": probability},
    }


def test_collecting_before_game_challenger_sample_floor():
    report = p49.build_candidate_report(
        [_receipt(i, 0.60, i % 2 == 0) for i in range(20)],
        min_samples=30,
        min_validation_samples=10,
        min_market_validation_samples=5,
    )
    assert report["state"] == "collecting"
    assert report["candidate"] is None
    assert report["autoApply"] is False
    assert report["productionApplied"] is False
    assert report["promotionGate"]["automaticApply"] is False
    assert report["safetyContract"]["providerRequests"] == 0


def test_overconfident_game_challenger_clears_forward_holdout_for_human_review():
    receipts = [
        _receipt(i, 0.80, i % 2 == 0, market_probability=0.50)
        for i in range(120)
    ]
    report = p49.build_candidate_report(
        receipts,
        min_samples=80,
        min_validation_samples=24,
        min_market_validation_samples=12,
        train_fraction=0.70,
        min_brier_improvement=0.005,
        max_ece_regression=0.01,
        max_market_skill_regression=0.005,
    )
    candidate = report["candidate"]
    assert candidate is not None
    assert candidate["validationIsForwardHoldout"] is True
    assert candidate["validationBrierImprovement"] >= 0.005
    assert candidate["validationMarketSkillDelta"] is not None
    assert candidate["validationMarketSkillDelta"] >= -0.005
    assert report["state"] == "review"
    assert report["promotionGate"]["eligible"] is True
    assert all(report["promotionGate"]["checks"].values())
    assert report["promotionGate"]["requiresHumanReview"] is True
    assert report["promotionGate"]["automaticApply"] is False


def test_market_skill_regression_blocks_otherwise_improving_challenger():
    receipts = []
    for i in range(90):
        receipts.append(
            _receipt(i, 0.80, i % 2 == 0, market_probability=0.50)
        )
    # Holdout: 18 unpaired 50% outcomes reward shrinking the champion, while
    # the 12 paired outcomes are genuinely 80% and expose a market-skill loss.
    for offset in range(18):
        i = 90 + offset
        receipts.append(
            _receipt(i, 0.80, offset % 2 == 0, market_probability=None)
        )
    paired_wins = 0
    for offset in range(12):
        i = 108 + offset
        won = offset < 10
        paired_wins += int(won)
        receipts.append(_receipt(i, 0.80, won, market_probability=0.80))
    assert paired_wins == 10

    report = p49.build_candidate_report(
        receipts,
        min_samples=80,
        min_validation_samples=24,
        min_market_validation_samples=12,
        train_fraction=0.75,
        min_brier_improvement=0.005,
        max_ece_regression=0.20,
        max_market_skill_regression=0.005,
    )
    candidate = report["candidate"]
    assert candidate is not None
    assert candidate["validationBrierImprovement"] >= 0.005
    assert candidate["validationMarketSkillDelta"] < -0.005
    assert report["promotionGate"]["checks"]["brierImprovement"] is True
    assert (
        report["promotionGate"]["checks"]["marketSkillRegressionBounded"]
        is False
    )
    assert report["promotionGate"]["eligible"] is False
    assert report["state"] == "rejected"


def test_missing_market_benchmark_blocks_game_challenger_promotion():
    receipts = [
        _receipt(i, 0.80, i % 2 == 0, market_probability=None)
        for i in range(120)
    ]
    report = p49.build_candidate_report(
        receipts,
        min_samples=80,
        min_validation_samples=24,
        min_market_validation_samples=12,
        train_fraction=0.70,
        min_brier_improvement=0.005,
        max_ece_regression=0.01,
        max_market_skill_regression=0.005,
    )
    assert report["candidate"] is not None
    assert (
        report["promotionGate"]["checks"]["marketBenchmarkSampleFloor"]
        is False
    )
    assert report["promotionGate"]["eligible"] is False


def test_well_calibrated_game_data_is_not_promoted():
    receipts = [
        _receipt(i, 0.60, (i % 5) < 3, market_probability=0.60)
        for i in range(120)
    ]
    report = p49.build_candidate_report(
        receipts,
        min_samples=80,
        min_validation_samples=24,
        min_market_validation_samples=12,
        train_fraction=0.70,
        min_brier_improvement=0.005,
        max_ece_regression=0.01,
        max_market_skill_regression=0.005,
    )
    assert report["promotionGate"]["eligible"] is False
    assert report["autoApply"] is False
    assert report["productionApplied"] is False


def test_game_candidate_probability_is_bounded():
    for probability in (0.0, 0.01, 0.50, 0.99, 1.0):
        calibrated = p49.calibrate_probability(
            probability, slope=1.5, intercept=-0.3
        )
        assert 0.001 <= calibrated <= 0.999


def test_forward_game_holdout_counts_are_deterministic():
    receipts = [
        _receipt(i, 0.70, i % 3 != 0, market_probability=0.65)
        for i in range(100)
    ]
    report = p49.build_candidate_report(
        receipts,
        min_samples=80,
        min_validation_samples=20,
        min_market_validation_samples=10,
        train_fraction=0.70,
    )
    candidate = report["candidate"]
    assert candidate is not None
    assert candidate["trainSamples"] == 70
    assert candidate["validationSamples"] == 30
    assert candidate["validationIsForwardHoldout"] is True


def test_production_report_fails_closed_when_game_ledger_unavailable(monkeypatch):
    monkeypatch.setattr(p49.p44, "ledger_status", lambda: {"available": False})
    report = p49.build_production_report()
    assert report["available"] is False
    assert report["state"] == "unavailable"
    assert report["autoApply"] is False
    assert report["productionApplied"] is False


def test_game_calibration_route_exposes_read_only_governance(client, monkeypatch):
    monkeypatch.setattr(
        p49,
        "build_production_report",
        lambda: {
            "available": True,
            "state": "collecting",
            "gradedSamples": 0,
            "autoApply": False,
            "productionApplied": False,
            "promotionGate": {"eligible": False, "automaticApply": False},
            "safetyContract": {"readOnly": True, "providerRequests": 0},
        },
    )
    response = client.get("/api/game-calibration/challenger")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["state"] == "collecting"
    assert payload["promotionGate"]["automaticApply"] is False
    assert payload["safetyContract"]["providerRequests"] == 0
