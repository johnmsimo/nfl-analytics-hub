from flask import Flask

from evaluation_v431 import run_held_out_evaluation
from lifecycle_v43 import (
    normalize_model_version,
    normalize_promotion_policy,
    transition_model_version,
)
from routes.v43_api import v43_bp

_ARTIFACT = "a" * 64
_CHAMPION_ARTIFACT = "b" * 64
_DATASET = "c" * 64
_EVIDENCE = "e" * 64


def _client():
    app = Flask(__name__)
    app.register_blueprint(v43_bp)
    return app.test_client()


def _candidate(version="v8", artifact=_ARTIFACT):
    registered = normalize_model_version(
        {
            "model_key": "win-probability",
            "version": version,
            "target": "home-win",
            "algorithm": "ensemble",
            "feature_schema": [{"name": "epa", "data_type": "number"}],
            "artifact": {"uri": f"s3://models/{version}.bin", "digest": artifact},
        },
        registered_at=80.0,
    )
    return transition_model_version(
        registered,
        "candidate",
        occurred_at=81.0,
        actor="worker",
        reason="evaluate",
    )


def _champion():
    candidate = _candidate("v7", _CHAMPION_ARTIFACT)
    return transition_model_version(
        candidate,
        "champion",
        occurred_at=82.0,
        actor="operator",
        reason="prior champion",
        promotion_decision={
            "policy_id": "policy-prior",
            "evaluation_id": "eval-prior",
            "evidence_digest": _EVIDENCE,
            "passed": True,
            "evaluated_at": 81.5,
        },
    )


def _evaluation(candidate, champion):
    return run_held_out_evaluation(
        {
            "policy": normalize_promotion_policy(
                {
                    "model_key": "win-probability",
                    "target": "home-win",
                    "minimum_samples": 3,
                    "metrics": [
                        {
                            "name": "brier-score",
                            "direction": "lower",
                            "threshold": 0.2,
                            "minimum_improvement": 0.01,
                        }
                    ],
                }
            ),
            "challenger": candidate,
            "champion": champion,
            "dataset_digest": _DATASET,
            "window": {"started_at": 80.0, "finished_at": 99.0},
            "observed_artifact_digest": _ARTIFACT,
            "expected_feature_schema_digest": candidate["feature_schema_digest"],
            "observations": [
                {"actual": 1, "candidate_prediction": 0.9, "champion_prediction": 0.7},
                {"actual": 0, "candidate_prediction": 0.1, "champion_prediction": 0.4},
                {"actual": 1, "candidate_prediction": 0.8, "champion_prediction": 0.6},
            ],
        },
        evaluated_at=100.0,
    )


def _trigger_payload():
    return {
        "evaluated_at": 100.0,
        "policy": {
            "model_key": "win-probability",
            "cooldown_seconds": 60,
            "signals": [
                {
                    "name": "feature-psi",
                    "kind": "feature-drift",
                    "threshold": 0.2,
                    "minimum_samples": 100,
                }
            ],
        },
        "model_version": _champion(),
        "signals": [
            {
                "name": "feature-psi",
                "value": 0.3,
                "sample_count": 500,
                "observed_at": 99.0,
                "evidence_digest": _EVIDENCE,
            }
        ],
    }


def _plan_payload():
    candidate = _candidate()
    champion = _champion()
    return {
        "planned_at": 102.0,
        "candidate": candidate,
        "champion": champion,
        "evaluation": _evaluation(candidate, champion),
        "selection_decided_at": 101.0,
        "mode": "canary",
        "steps": [
            {
                "name": "ten-percent",
                "candidate_traffic_percent": 10,
                "minimum_observation_seconds": 60,
            },
            {
                "name": "full",
                "candidate_traffic_percent": 100,
                "minimum_observation_seconds": 60,
            },
        ],
        "health_gates": [
            {
                "name": "error-rate",
                "direction": "lower",
                "threshold": 0.02,
                "minimum_samples": 100,
            }
        ],
    }


def test_capabilities_expose_v432_controls_and_endpoints():
    response = _client().get("/api/v4.3/capabilities")
    body = response.get_json()
    assert response.status_code == 200
    assert body["version"] == "4.3.2"
    assert body["features"]["distributed_retraining_requests"] is True
    assert body["features"]["automatic_training"] is False
    assert body["endpoints"]["rollout_step_evaluate"].endswith("/rollouts/steps/evaluate")


def test_trigger_and_retraining_request_endpoints_integrate_with_v42_jobs():
    client = _client()
    trigger = client.post(
        "/api/v4.3/models/retraining/triggers/evaluate",
        json=_trigger_payload(),
    ).get_json()
    assert trigger["triggered"] is True
    response = client.post(
        "/api/v4.3/models/retraining/requests/normalize",
        json={
            "trigger": trigger,
            "model_version": _champion(),
            "requested_version": "v9",
            "dataset_digest": _DATASET,
            "code_version": "git:abc123",
            "output_artifact_uri": "s3://models/v9.bin",
            "requested_by": "operator",
            "requested_at": 101.0,
        },
    )
    body = response.get_json()
    assert response.status_code == 200
    assert body["job"]["version"] == "4.2.0"
    assert body["job"]["job_type"] == "model.retraining.request"


def test_rollout_plan_and_step_endpoints_emit_control_intent_only():
    client = _client()
    plan_response = client.post(
        "/api/v4.3/models/rollouts/plans/normalize",
        json=_plan_payload(),
    )
    plan = plan_response.get_json()
    assert plan_response.status_code == 200
    assert plan["automatic_traffic_mutation"] is False
    step_response = client.post(
        "/api/v4.3/models/rollouts/steps/evaluate",
        json={
            "rollout_plan": plan,
            "step_index": 0,
            "step_started_at": 120.0,
            "evaluated_at": 200.0,
            "health_observations": [
                {
                    "name": "error-rate",
                    "value": 0.01,
                    "sample_count": 500,
                    "observed_at": 199.0,
                    "evidence_digest": _EVIDENCE,
                }
            ],
        },
    )
    decision = step_response.get_json()
    assert step_response.status_code == 200
    assert decision["action"] == "advance"
    assert decision["automatic_traffic_mutation"] is False


def test_rollout_endpoint_returns_explicit_rollback_target():
    client = _client()
    plan = client.post(
        "/api/v4.3/models/rollouts/plans/normalize",
        json=_plan_payload(),
    ).get_json()
    response = client.post(
        "/api/v4.3/models/rollouts/steps/evaluate",
        json={
            "rollout_plan": plan,
            "step_index": 0,
            "step_started_at": 120.0,
            "evaluated_at": 200.0,
            "health_observations": [
                {
                    "name": "error-rate",
                    "value": 0.2,
                    "sample_count": 500,
                    "observed_at": 199.0,
                    "evidence_digest": _EVIDENCE,
                }
            ],
        },
    )
    body = response.get_json()
    assert response.status_code == 200
    assert body["action"] == "rollback"
    assert body["rollback_target"]["artifact_digest"] == f"sha256:{_CHAMPION_ARTIFACT}"


def test_v432_routes_reject_malformed_or_unsafe_inputs():
    client = _client()
    assert (
        client.post(
            "/api/v4.3/models/retraining/triggers/evaluate",
            json={"signals": "invalid"},
        ).status_code
        == 400
    )
    unsafe = _plan_payload()
    unsafe["steps"] = [{"name": "partial", "candidate_traffic_percent": 10}]
    response = client.post(
        "/api/v4.3/models/rollouts/plans/normalize",
        json=unsafe,
    )
    assert response.status_code == 400
    assert "100 percent" in response.get_json()["error"]
