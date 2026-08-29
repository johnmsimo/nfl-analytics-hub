from __future__ import annotations

import math

import p49_game_calibration as p49
import p51_game_calibration_guard as p51


CANDIDATE = "p49-post-promotion-test"


def _event(*, slope: float, intercept: float) -> dict:
    return {
        "eventId": "p50-event",
        "action": "promote",
        "candidateId": CANDIDATE,
        "family": "logit-affine",
        "parameters": {"slope": slope, "intercept": intercept},
        "approvedBy": "owner",
        "createdAt": "2026-09-01T00:00:00+00:00",
    }


def _champion(*, slope: float, intercept: float) -> dict:
    return {
        "available": True,
        "state": "promoted",
        "applied": True,
        "candidateId": CANDIDATE,
        "family": "logit-affine",
        "parameters": {"slope": slope, "intercept": intercept},
        "approvedAt": "2026-09-01T00:00:00+00:00",
    }


def _receipts(
    count: int,
    *,
    baseline_probability: float,
    slope: float,
    intercept: float,
    wins: int | None = None,
) -> list[dict]:
    promoted = p49.calibrate_probability(
        baseline_probability, slope=slope, intercept=intercept
    )
    wins = count // 2 if wins is None else wins
    rows = []
    for idx in range(count):
        grade = "win" if idx < wins else "loss"
        rows.append(
            {
                "receiptId": f"r-{idx:03d}",
                "releasedAt": f"2026-09-{(idx % 20) + 1:02d}T00:00:00+00:00",
                "grade": grade,
                "release": {
                    "marketKey": "moneyline",
                    "modelProbability": promoted,
                    "fairMarketProbability": 0.55,
                    "sourceModelVersion": f"p40-transparent-v1+{CANDIDATE}",
                },
                "result": {"probability": promoted},
            }
        )
    return rows


def test_inverse_calibration_round_trip():
    original = 0.73
    promoted = p49.calibrate_probability(original, slope=0.85, intercept=0.02)
    recovered = p51.inverse_calibrate_probability(
        promoted, slope=0.85, intercept=0.02
    )
    assert recovered is not None
    assert math.isclose(recovered, original, rel_tol=0, abs_tol=1e-9)


def test_inverse_calibration_rejects_floor_saturation():
    assert p51.inverse_calibrate_probability(0.5, slope=0.85, intercept=-0.3) is None


def test_guard_returns_baseline_when_no_promoted_champion():
    report = p51.build_guard_report([], [], {"state": "baseline", "applied": False})
    assert report["state"] == "baseline"
    assert report["recommendation"] == "NO_PROMOTED_CHAMPION"
    assert report["rollbackGate"]["recommended"] is False
    assert report["safetyContract"]["automaticRollback"] is False


def test_guard_collects_until_post_promotion_sample_floor():
    slope, intercept = 0.5, 0.0
    report = p51.build_guard_report(
        _receipts(10, baseline_probability=0.8, slope=slope, intercept=intercept),
        [_event(slope=slope, intercept=intercept)],
        _champion(slope=slope, intercept=intercept),
    )
    assert report["state"] == "collecting"
    assert report["gradedSamples"] == 10
    assert report["rollbackGate"]["recommended"] is False


def test_guard_keeps_champion_when_it_beats_shadow_baseline():
    slope, intercept = 0.5, 0.0
    report = p51.build_guard_report(
        _receipts(20, baseline_probability=0.8, slope=slope, intercept=intercept),
        [_event(slope=slope, intercept=intercept)],
        _champion(slope=slope, intercept=intercept),
    )
    assert report["state"] == "healthy"
    assert report["recommendation"] == "KEEP_PROMOTED_CHAMPION"
    assert report["rollbackGate"]["recommended"] is False
    assert report["monitor"]["brierDeltaVsShadow"] < 0


def test_guard_recommends_human_rollback_review_on_post_promotion_regression():
    slope, intercept = 1.5, 0.3
    report = p51.build_guard_report(
        _receipts(20, baseline_probability=0.6, slope=slope, intercept=intercept),
        [_event(slope=slope, intercept=intercept)],
        _champion(slope=slope, intercept=intercept),
    )
    assert report["state"] == "rollback-review"
    assert report["recommendation"] == "REVIEW_ROLLBACK_TO_BASELINE"
    assert report["rollbackGate"]["recommended"] is True
    assert report["rollbackGate"]["automaticRollback"] is False
    assert report["monitor"]["brierDeltaVsShadow"] > p51.policy()["maxBrierRegressionVsShadow"]


def test_guard_ignores_non_moneyline_and_other_candidate_receipts():
    slope, intercept = 0.5, 0.0
    rows = _receipts(20, baseline_probability=0.8, slope=slope, intercept=intercept)
    rows.append(
        {
            "receiptId": "spread",
            "releasedAt": "2026-09-21T00:00:00+00:00",
            "grade": "win",
            "release": {
                "marketKey": "spread",
                "modelProbability": 0.7,
                "sourceModelVersion": f"p40-transparent-v1+{CANDIDATE}",
            },
        }
    )
    rows.append(
        {
            "receiptId": "other",
            "releasedAt": "2026-09-22T00:00:00+00:00",
            "grade": "win",
            "release": {
                "marketKey": "moneyline",
                "modelProbability": 0.7,
                "sourceModelVersion": "p40-transparent-v1+p49-other",
            },
        }
    )
    report = p51.build_guard_report(
        rows,
        [_event(slope=slope, intercept=intercept)],
        _champion(slope=slope, intercept=intercept),
    )
    assert report["gradedSamples"] == 20
