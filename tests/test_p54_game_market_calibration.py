from __future__ import annotations

import time

import p41_game_market_pricing as p41
import p54_game_market_calibration as p54


def _receipt(
    idx: int,
    *,
    market: str,
    probability: float = 0.80,
    won: bool,
    market_probability: float = 0.50,
) -> dict:
    return {
        "receiptId": f"{market}-{idx:04d}",
        "releasedAt": f"2026-09-{(idx // 24) + 1:02d}T{idx % 24:02d}:00:00+00:00",
        "grade": "win" if won else "loss",
        "release": {
            "gameId": f"{market}-game-{idx:04d}",
            "marketKey": market,
            "modelProbability": probability,
            "fairMarketProbability": market_probability,
        },
        "result": {"probability": probability},
    }


def _eligible_report(market: str = "spread") -> dict:
    receipts = [
        _receipt(i, market=market, won=i % 2 == 0)
        for i in range(80)
    ]
    return p54.build_market_candidate_report(receipts, market)


def test_market_candidate_collects_before_market_sample_floor():
    report = p54.build_market_candidate_report(
        [_receipt(i, market="spread", won=i % 2 == 0) for i in range(20)],
        "spread",
    )
    assert report["state"] == "collecting"
    assert report["candidate"] is None
    assert report["promotionGate"]["eligible"] is False
    assert report["safetyContract"]["marketIsolatedTraining"] is True


def test_spread_challenger_can_clear_market_isolated_forward_holdout():
    report = _eligible_report("spread")
    assert report["state"] == "review"
    assert report["promotionGate"]["eligible"] is True
    assert all(report["promotionGate"]["checks"].values())
    candidate = report["candidate"]
    assert candidate["market"] == "spread"
    assert candidate["validationIsForwardHoldout"] is True
    assert candidate["validationBrierImprovement"] >= p54.policy()["minBrierImprovement"]


def test_market_training_never_borrows_other_market_receipts():
    receipts = [
        _receipt(i, market="spread", won=i % 2 == 0)
        for i in range(60)
    ] + [
        _receipt(i, market="total", won=True, probability=0.95)
        for i in range(60)
    ]
    report = p54.build_market_candidate_report(receipts, "spread")
    assert report["gradedSamples"] == 60
    assert report["candidate"]["market"] == "spread"
    assert report["promotionGate"]["checks"]["marketIsolatedTraining"] is True


def test_market_promotion_requires_exact_confirmation():
    report = _eligible_report("spread")
    result = p54.promote_candidate(
        "spread",
        report["candidate"]["candidateId"],
        confirmation="YES",
        actor="owner",
        persist=False,
        report=report,
    )
    assert result["ok"] is False
    assert result["code"] == "CONFIRMATION_REQUIRED"


def test_market_promotion_dry_run_requires_exact_current_candidate():
    report = _eligible_report("total")
    mismatch = p54.promote_candidate(
        "total",
        "p54-to-wrong",
        confirmation=p54.PROMOTE_CONFIRMATION,
        actor="owner",
        persist=False,
        report=report,
    )
    assert mismatch["ok"] is False
    assert mismatch["code"] == "CANDIDATE_MISMATCH"

    result = p54.promote_candidate(
        "total",
        report["candidate"]["candidateId"],
        confirmation=p54.PROMOTE_CONFIRMATION,
        actor="owner",
        persist=False,
        report=report,
    )
    assert result["ok"] is True
    assert result["dryRun"] is True
    assert result["event"]["market"] == "total"


def test_market_calibration_never_flips_selected_side_probability():
    champion = {
        "state": "promoted",
        "applied": True,
        "candidateId": "p54-sp-test",
        "family": "logit-affine",
        "parameters": {"slope": 0.5, "intercept": -0.3},
        "approvedAt": "2026-09-01T00:00:00+00:00",
    }
    result = p54.apply_to_selected_probability("spread", 0.51, champion=champion)
    assert result["applied"] is True
    assert 0.5 <= result["probability"] <= 0.999
    assert result["candidateId"] == "p54-sp-test"


def test_p41_applies_market_specific_calibration_after_side_selection(monkeypatch):
    def fake_calibration(market: str, probability: float) -> dict:
        target = 0.71 if market == "spread" else 0.66
        return {
            "probability": target,
            "rawProbability": probability,
            "applied": True,
            "market": market,
            "candidateId": f"p54-{market[:2]}-test",
            "modelVersion": p54.MODEL_VERSION,
            "championState": "promoted",
        }

    monkeypatch.setattr(p41, "_market_calibration", fake_calibration)
    decision = {
        "gameId": "game-1",
        "season": 2026,
        "seasonType": "REG",
        "week": 1,
        "kickoffAt": "2026-09-10T00:15:00Z",
        "homeTeam": "SEA",
        "awayTeam": "NE",
        "modelVersion": "p40-transparent-v1",
        "homeWinProbability": 0.65,
        "modelHomeMargin": 7.0,
        "confidenceScore": 82.0,
        "decisionGrade": "Play",
        "evidence": {
            "home": {"basic": {"ppg": 28.0, "papg": 20.0}},
            "away": {"basic": {"ppg": 21.0, "papg": 24.0}},
        },
    }
    now = time.time()
    event = {
        "id": "odds-1",
        "home_team": "SEA",
        "away_team": "NE",
        "commence_time": "2026-09-10T00:15:00Z",
        "bookmakers": [
            {
                "key": "book-a",
                "title": "Book A",
                "markets": [
                    {
                        "key": "h2h",
                        "outcomes": [
                            {"name": "SEA", "price": -130},
                            {"name": "NE", "price": 110},
                        ],
                    },
                    {
                        "key": "spreads",
                        "outcomes": [
                            {"name": "SEA", "price": -110, "point": -3.5},
                            {"name": "NE", "price": -110, "point": 3.5},
                        ],
                    },
                    {
                        "key": "totals",
                        "outcomes": [
                            {"name": "Over", "price": -110, "point": 44.5},
                            {"name": "Under", "price": -110, "point": 44.5},
                        ],
                    },
                ],
            }
        ],
    }
    priced = p41.price_game_decision(decision, event, fetched_at=now)
    spread = priced["markets"]["spread"]
    total = priced["markets"]["total"]
    assert spread["modelProbability"] == 0.71
    assert spread["prePromotionProbability"] != spread["modelProbability"]
    assert spread["marketCalibration"]["candidateId"] == "p54-sp-test"
    assert spread["marketModelVersion"].endswith("+p54-sp-test")
    assert total["modelProbability"] == 0.66
    assert total["marketCalibration"]["candidateId"] == "p54-to-test"


def test_market_calibration_status_route_is_read_only(client, monkeypatch):
    monkeypatch.setattr(
        p54,
        "build_production_report",
        lambda: {
            "available": True,
            "state": "collecting",
            "markets": {
                "spread": {"state": "collecting", "gradedSamples": 0},
                "total": {"state": "collecting", "gradedSamples": 0},
            },
            "safetyContract": {
                "providerRequests": 0,
                "automaticPromotion": False,
                "automaticRollback": False,
            },
        },
    )
    response = client.get("/api/game-market-calibration/status")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["state"] == "collecting"
    assert payload["safetyContract"]["providerRequests"] == 0
    assert payload["safetyContract"]["automaticPromotion"] is False
