from __future__ import annotations

import math

import p49_game_calibration as p49
import p55_game_market_calibration_guard as p55


SPREAD_CANDIDATE = "p54-sp-post-promotion-test"
TOTAL_CANDIDATE = "p54-to-post-promotion-test"


def _event(market: str, *, slope: float, intercept: float) -> dict:
    candidate = SPREAD_CANDIDATE if market == "spread" else TOTAL_CANDIDATE
    return {
        "eventId": f"p54-{market}-event",
        "market": market,
        "action": "promote",
        "candidateId": candidate,
        "family": "logit-affine",
        "parameters": {"slope": slope, "intercept": intercept},
        "approvedBy": "owner",
        "createdAt": "2026-09-01T00:00:00+00:00",
    }


def _champion(market: str, *, slope: float, intercept: float) -> dict:
    candidate = SPREAD_CANDIDATE if market == "spread" else TOTAL_CANDIDATE
    return {
        "available": True,
        "market": market,
        "state": "promoted",
        "applied": True,
        "candidateId": candidate,
        "family": "logit-affine",
        "parameters": {"slope": slope, "intercept": intercept},
        "approvedAt": "2026-09-01T00:00:00+00:00",
    }


def _receipts(
    market: str,
    count: int,
    *,
    baseline_probability: float,
    slope: float,
    intercept: float,
    wins: int | None = None,
) -> list[dict]:
    candidate = SPREAD_CANDIDATE if market == "spread" else TOTAL_CANDIDATE
    promoted = p49.calibrate_probability(
        baseline_probability, slope=slope, intercept=intercept
    )
    wins = count // 2 if wins is None else wins
    return [
        {
            "receiptId": f"{market}-{idx:03d}",
            "releasedAt": f"2026-09-{(idx % 20) + 1:02d}T00:00:00+00:00",
            "grade": "win" if idx < wins else "loss",
            "release": {
                "marketKey": market,
                "modelProbability": promoted,
                "fairMarketProbability": 0.55,
                "sourceModelVersion": f"p41-pricing-v1+{candidate}",
            },
            "result": {"probability": promoted},
        }
        for idx in range(count)
    ]


def test_inverse_market_calibration_round_trip():
    original = 0.73
    promoted = p49.calibrate_probability(original, slope=0.85, intercept=0.02)
    recovered = p55.inverse_calibrate_probability(
        promoted, slope=0.85, intercept=0.02
    )
    assert recovered is not None
    assert math.isclose(recovered, original, rel_tol=0, abs_tol=1e-9)


def test_inverse_market_calibration_rejects_floor_saturation():
    assert p55.inverse_calibrate_probability(0.5, slope=0.85, intercept=-0.3) is None


def test_market_guard_returns_baseline_without_promoted_champion():
    report = p55.build_market_guard_report(
        [],
        [],
        "spread",
        {"state": "baseline", "applied": False},
    )
    assert report["state"] == "baseline"
    assert report["recommendation"] == "NO_PROMOTED_MARKET_CHAMPION"
    assert report["rollbackGate"]["recommended"] is False
    assert report["safetyContract"]["marketIsolated"] is True


def test_market_guard_collects_until_per_market_sample_floor():
    slope, intercept = 0.5, 0.0
    report = p55.build_market_guard_report(
        _receipts("spread", 10, baseline_probability=0.8, slope=slope, intercept=intercept),
        [_event("spread", slope=slope, intercept=intercept)],
        "spread",
        _champion("spread", slope=slope, intercept=intercept),
    )
    assert report["state"] == "collecting"
    assert report["gradedSamples"] == 10
    assert report["rollbackGate"]["recommended"] is False


def test_market_guard_keeps_champion_when_it_beats_shadow():
    slope, intercept = 0.5, 0.0
    report = p55.build_market_guard_report(
        _receipts("total", 20, baseline_probability=0.8, slope=slope, intercept=intercept),
        [_event("total", slope=slope, intercept=intercept)],
        "total",
        _champion("total", slope=slope, intercept=intercept),
    )
    assert report["state"] == "healthy"
    assert report["recommendation"] == "KEEP_PROMOTED_MARKET_CHAMPION"
    assert report["rollbackGate"]["recommended"] is False
    assert report["monitor"]["brierDeltaVsShadow"] < 0


def test_market_guard_recommends_human_rollback_on_regression():
    slope, intercept = 1.5, 0.3
    report = p55.build_market_guard_report(
        _receipts("spread", 20, baseline_probability=0.6, slope=slope, intercept=intercept),
        [_event("spread", slope=slope, intercept=intercept)],
        "spread",
        _champion("spread", slope=slope, intercept=intercept),
    )
    assert report["state"] == "rollback-review"
    assert report["recommendation"] == "REVIEW_MARKET_ROLLBACK_TO_BASELINE"
    assert report["rollbackGate"]["recommended"] is True
    assert report["rollbackGate"]["automaticRollback"] is False
    assert report["monitor"]["brierDeltaVsShadow"] > p55.policy()["maxBrierRegressionVsShadow"]


def test_market_guard_never_borrows_other_market_receipts():
    slope, intercept = 0.5, 0.0
    spread_rows = _receipts(
        "spread", 20, baseline_probability=0.8, slope=slope, intercept=intercept
    )
    total_rows = _receipts(
        "total", 20, baseline_probability=0.95, slope=1.5, intercept=0.3, wins=0
    )
    report = p55.build_market_guard_report(
        spread_rows + total_rows,
        [
            _event("spread", slope=slope, intercept=intercept),
            _event("total", slope=1.5, intercept=0.3),
        ],
        "spread",
        _champion("spread", slope=slope, intercept=intercept),
    )
    assert report["gradedSamples"] == 20
    assert report["state"] == "healthy"
    assert report["rollbackGate"]["checks"]["marketIsolation"] is True


def test_aggregate_guard_can_flag_one_market_without_affecting_other():
    healthy_slope, healthy_intercept = 0.5, 0.0
    bad_slope, bad_intercept = 1.5, 0.3
    receipts = _receipts(
        "spread",
        20,
        baseline_probability=0.8,
        slope=healthy_slope,
        intercept=healthy_intercept,
    ) + _receipts(
        "total",
        20,
        baseline_probability=0.6,
        slope=bad_slope,
        intercept=bad_intercept,
    )
    report = p55.build_guard_report(
        receipts,
        [
            _event("spread", slope=healthy_slope, intercept=healthy_intercept),
            _event("total", slope=bad_slope, intercept=bad_intercept),
        ],
        {
            "spread": _champion(
                "spread", slope=healthy_slope, intercept=healthy_intercept
            ),
            "total": _champion("total", slope=bad_slope, intercept=bad_intercept),
        },
    )
    assert report["state"] == "rollback-review"
    assert report["markets"]["spread"]["state"] == "healthy"
    assert report["markets"]["total"]["state"] == "rollback-review"
    assert report["rollbackReviewMarkets"] == ["total"]


def test_market_guard_status_route_is_read_only(client, monkeypatch):
    monkeypatch.setattr(
        p55,
        "build_production_report",
        lambda: {
            "available": True,
            "state": "baseline",
            "markets": {
                "spread": {"state": "baseline"},
                "total": {"state": "baseline"},
            },
            "rollbackReviewMarkets": [],
            "safetyContract": {
                "readOnly": True,
                "providerRequests": 0,
                "automaticRollback": False,
            },
        },
    )
    response = client.get("/api/game-market-calibration/guard")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["state"] == "baseline"
    assert payload["safetyContract"]["providerRequests"] == 0
    assert payload["safetyContract"]["automaticRollback"] is False
