from __future__ import annotations

import p56_game_market_calibration_control_plane as p56


SPREAD_CANDIDATE = "p54-sp-p56-test"
TOTAL_CANDIDATE = "p54-to-p56-test"


def _candidate(market: str) -> str:
    return SPREAD_CANDIDATE if market == "spread" else TOTAL_CANDIDATE


def _challenger(
    market: str,
    *,
    state: str = "review",
    eligible: bool = True,
    candidate: bool = True,
    samples: int = 80,
) -> dict:
    return {
        "available": True,
        "market": market,
        "state": state,
        "gradedSamples": samples,
        "candidate": {"candidateId": _candidate(market)} if candidate else None,
        "promotionGate": {
            "eligible": eligible,
            "requiresHumanReview": True,
            "automaticApply": False,
        },
    }


def _champion(market: str, *, applied: bool = False) -> dict:
    return {
        "available": True,
        "market": market,
        "state": "promoted" if applied else "baseline",
        "applied": applied,
        "candidateId": _candidate(market) if applied else None,
    }


def _guard(
    market: str,
    *,
    state: str = "baseline",
    rollback: bool = False,
    samples: int = 0,
) -> dict:
    return {
        "available": True,
        "market": market,
        "state": state,
        "gradedSamples": samples,
        "rollbackGate": {
            "recommended": rollback,
            "requiresHumanReview": True,
            "automaticRollback": False,
        },
    }


def test_market_control_plane_surfaces_promotion_review_when_p54_clears():
    report = p56.build_market_control_plane(
        "spread",
        _challenger("spread"),
        _champion("spread", applied=False),
        _guard("spread"),
    )
    assert report["state"] == "promotion-review"
    assert report["recommendedAction"] == "REVIEW_MARKET_PROMOTION"
    assert report["promoteReady"] is True
    assert report["rollbackReady"] is False
    assert report["commands"]["promotion"]["allowed"] is True
    assert report["commands"]["promotion"]["market"] == "spread"
    assert report["commands"]["promotion"]["candidateId"] == SPREAD_CANDIDATE
    assert report["commands"]["promotion"]["confirmation"] == "PROMOTE_GAME_MARKET_CALIBRATION"


def test_market_control_plane_fails_closed_when_p54_gate_fails():
    report = p56.build_market_control_plane(
        "total",
        _challenger("total", state="rejected", eligible=False),
        _champion("total", applied=False),
        _guard("total"),
    )
    assert report["promoteReady"] is False
    assert report["commands"]["promotion"]["allowed"] is False
    assert "challenger_not_in_review_state" in report["blockers"]
    assert "p54_market_promotion_gate_not_eligible" in report["blockers"]


def test_active_collecting_market_champion_is_kept_without_rollback():
    report = p56.build_market_control_plane(
        "spread",
        _challenger("spread", state="collecting", eligible=False, candidate=False),
        _champion("spread", applied=True),
        _guard("spread", state="collecting", rollback=False, samples=10),
    )
    assert report["state"] == "champion-collecting"
    assert report["recommendedAction"] == "KEEP_AND_COLLECT"
    assert report["rollbackReady"] is False
    assert report["commands"]["rollback"]["allowed"] is True
    assert report["commands"]["rollback"]["recommended"] is False


def test_healthy_market_champion_remains_active():
    report = p56.build_market_control_plane(
        "total",
        _challenger("total", state="rejected", eligible=False, candidate=False),
        _champion("total", applied=True),
        _guard("total", state="healthy", rollback=False, samples=30),
    )
    assert report["state"] == "champion-healthy"
    assert report["recommendedAction"] == "KEEP_MARKET_CHAMPION"
    assert report["rollbackReady"] is False


def test_p55_regression_surfaces_market_specific_human_rollback_review():
    report = p56.build_market_control_plane(
        "spread",
        _challenger("spread", state="review", eligible=True),
        _champion("spread", applied=True),
        _guard("spread", state="rollback-review", rollback=True, samples=30),
    )
    assert report["state"] == "rollback-review"
    assert report["recommendedAction"] == "REVIEW_MARKET_ROLLBACK"
    assert report["promoteReady"] is False
    assert report["rollbackReady"] is True
    assert report["commands"]["rollback"]["allowed"] is True
    assert report["commands"]["rollback"]["recommended"] is True
    assert report["commands"]["rollback"]["confirmation"] == "ROLLBACK_GAME_MARKET_CALIBRATION"


def test_collecting_market_challenger_does_not_invent_candidate():
    report = p56.build_market_control_plane(
        "total",
        _challenger("total", state="collecting", eligible=False, candidate=False),
        _champion("total", applied=False),
        _guard("total"),
    )
    assert report["state"] == "challenger-collecting"
    assert report["candidateId"] is None
    assert report["promoteReady"] is False
    assert report["commands"]["promotion"]["candidateId"] is None


def test_aggregate_control_plane_keeps_spread_and_total_isolated():
    calibration = {
        "available": True,
        "markets": {
            "spread": _challenger("spread", state="rejected", eligible=False, candidate=False),
            "total": _challenger("total", state="review", eligible=True),
        },
        "champions": {
            "spread": _champion("spread", applied=True),
            "total": _champion("total", applied=False),
        },
    }
    guard = {
        "available": True,
        "markets": {
            "spread": _guard("spread", state="healthy", rollback=False, samples=30),
            "total": _guard("total", state="baseline", rollback=False),
        },
    }
    report = p56.build_control_plane(calibration, guard)
    assert report["state"] == "promotion-review"
    assert report["promotionReviewMarkets"] == ["total"]
    assert report["rollbackReviewMarkets"] == []
    assert report["activeChampionMarkets"] == ["spread"]
    assert report["healthyChampionMarkets"] == ["spread"]
    assert report["markets"]["spread"]["state"] == "champion-healthy"
    assert report["markets"]["total"]["state"] == "promotion-review"


def test_aggregate_rollback_review_has_priority_over_other_market_promotion():
    calibration = {
        "available": True,
        "markets": {
            "spread": _challenger("spread", state="rejected", eligible=False, candidate=False),
            "total": _challenger("total", state="review", eligible=True),
        },
        "champions": {
            "spread": _champion("spread", applied=True),
            "total": _champion("total", applied=False),
        },
    }
    guard = {
        "available": True,
        "markets": {
            "spread": _guard("spread", state="rollback-review", rollback=True, samples=30),
            "total": _guard("total", state="baseline", rollback=False),
        },
    }
    report = p56.build_control_plane(calibration, guard)
    assert report["state"] == "rollback-review"
    assert report["rollbackReviewMarkets"] == ["spread"]
    assert report["promotionReviewMarkets"] == ["total"]
    assert report["markets"]["spread"]["rollbackReady"] is True
    assert report["markets"]["total"]["promoteReady"] is True


def test_market_control_plane_route_is_read_only(client, monkeypatch):
    monkeypatch.setattr(
        p56,
        "build_production_control_plane",
        lambda: {
            "available": True,
            "state": "baseline-monitor",
            "recommendedAction": "KEEP_MARKET_BASELINES",
            "markets": {
                "spread": {"state": "baseline-monitor"},
                "total": {"state": "baseline-monitor"},
            },
            "promotionReviewMarkets": [],
            "rollbackReviewMarkets": [],
            "safetyContract": {
                "readOnly": True,
                "marketIsolated": True,
                "providerRequests": 0,
                "automaticPromotion": False,
                "automaticRollback": False,
            },
        },
    )
    response = client.get("/api/game-market-calibration/control-plane")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["state"] == "baseline-monitor"
    assert payload["safetyContract"]["providerRequests"] == 0
    assert payload["safetyContract"]["automaticPromotion"] is False
    assert payload["safetyContract"]["automaticRollback"] is False
