from __future__ import annotations

import p52_game_calibration_control_plane as p52


CANDIDATE = "p49-p52-test"


def _challenger(*, state: str = "review", eligible: bool = True, candidate: bool = True) -> dict:
    return {
        "available": True,
        "state": state,
        "gradedSamples": 120,
        "candidate": {"candidateId": CANDIDATE} if candidate else None,
        "promotionGate": {
            "eligible": eligible,
            "requiresHumanReview": True,
            "automaticApply": False,
        },
    }


def _champion_status(*, applied: bool = False, p50_eligible: bool = True) -> dict:
    return {
        "available": True,
        "champion": {
            "state": "promoted" if applied else "baseline",
            "applied": applied,
            "candidateId": CANDIDATE if applied else None,
        },
        "promotionReview": {
            "eligible": p50_eligible,
            "candidateId": CANDIDATE,
        },
    }


def _guard(*, state: str = "baseline", rollback: bool = False, samples: int = 0) -> dict:
    return {
        "available": True,
        "state": state,
        "gradedSamples": samples,
        "rollbackGate": {
            "recommended": rollback,
            "requiresHumanReview": True,
            "automaticRollback": False,
        },
    }


def test_control_plane_surfaces_promotion_review_when_all_gates_clear():
    report = p52.build_control_plane(
        _challenger(),
        _champion_status(applied=False, p50_eligible=True),
        _guard(),
    )
    assert report["state"] == "promotion-review"
    assert report["recommendedAction"] == "REVIEW_PROMOTION"
    assert report["promoteReady"] is True
    assert report["rollbackReady"] is False
    assert report["commands"]["promotion"]["allowed"] is True
    assert report["commands"]["promotion"]["candidateId"] == CANDIDATE
    assert report["commands"]["promotion"]["confirmation"] == "PROMOTE_GAME_CALIBRATION"
    assert report["safetyContract"]["automaticPromotion"] is False


def test_control_plane_fails_closed_when_p50_gate_does_not_clear():
    report = p52.build_control_plane(
        _challenger(),
        _champion_status(applied=False, p50_eligible=False),
        _guard(),
    )
    assert report["promoteReady"] is False
    assert report["commands"]["promotion"]["allowed"] is False
    assert "p50_moneyline_promotion_gate_not_eligible" in report["blockers"]


def test_active_collecting_champion_is_kept_without_rollback():
    report = p52.build_control_plane(
        _challenger(state="collecting", eligible=False, candidate=False),
        _champion_status(applied=True, p50_eligible=False),
        _guard(state="collecting", rollback=False, samples=10),
    )
    assert report["state"] == "champion-collecting"
    assert report["recommendedAction"] == "KEEP_AND_COLLECT"
    assert report["rollbackReady"] is False
    assert report["commands"]["rollback"]["allowed"] is True
    assert report["commands"]["rollback"]["recommended"] is False


def test_healthy_champion_remains_active():
    report = p52.build_control_plane(
        _challenger(state="rejected", eligible=False, candidate=False),
        _champion_status(applied=True, p50_eligible=False),
        _guard(state="healthy", rollback=False, samples=30),
    )
    assert report["state"] == "champion-healthy"
    assert report["recommendedAction"] == "KEEP_CHAMPION"
    assert report["rollbackReady"] is False


def test_guard_regression_surfaces_human_rollback_review_only():
    report = p52.build_control_plane(
        _challenger(state="review", eligible=True),
        _champion_status(applied=True, p50_eligible=True),
        _guard(state="rollback-review", rollback=True, samples=30),
    )
    assert report["state"] == "rollback-review"
    assert report["recommendedAction"] == "REVIEW_ROLLBACK"
    assert report["promoteReady"] is False
    assert report["rollbackReady"] is True
    assert report["commands"]["rollback"]["allowed"] is True
    assert report["commands"]["rollback"]["recommended"] is True
    assert report["safetyContract"]["automaticRollback"] is False


def test_collecting_challenger_does_not_manufacture_candidate_or_action():
    report = p52.build_control_plane(
        _challenger(state="collecting", eligible=False, candidate=False),
        _champion_status(applied=False, p50_eligible=False),
        _guard(),
    )
    assert report["state"] == "challenger-collecting"
    assert report["recommendedAction"] == "COLLECT_MORE_RESULTS"
    assert report["candidateId"] is None
    assert report["promoteReady"] is False
    assert report["commands"]["promotion"]["candidateId"] is None


def test_control_plane_route_exposes_read_only_governance(client, monkeypatch):
    monkeypatch.setattr(
        p52,
        "build_production_control_plane",
        lambda: {
            "available": True,
            "state": "baseline-monitor",
            "recommendedAction": "KEEP_BASELINE",
            "promoteReady": False,
            "rollbackReady": False,
            "safetyContract": {
                "readOnly": True,
                "providerRequests": 0,
                "automaticPromotion": False,
                "automaticRollback": False,
            },
        },
    )
    response = client.get("/api/game-calibration/control-plane")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["state"] == "baseline-monitor"
    assert payload["safetyContract"]["providerRequests"] == 0
    assert payload["safetyContract"]["automaticPromotion"] is False
    assert payload["safetyContract"]["automaticRollback"] is False
