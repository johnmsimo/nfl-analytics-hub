from flask import Flask

from lifecycle_v43 import (
    normalize_model_version,
    normalize_promotion_policy,
    transition_model_version,
)
from routes.v43_api import v43_bp

_ARTIFACT = "a" * 64
_DATASET = "c" * 64
_EVIDENCE = "e" * 64


def _client():
    app = Flask(__name__)
    app.register_blueprint(v43_bp)
    return app.test_client()


def _candidate(version: str):
    registered = normalize_model_version(
        {
            "model_key": "win-probability",
            "version": version,
            "target": "home-win",
            "algorithm": "calibrated-ensemble",
            "feature_schema": [{"name": "home-epa", "data_type": "number", "source": "warehouse"}],
            "artifact": {
                "uri": f"s3://models/win-probability/{version}.bin",
                "digest": _ARTIFACT,
            },
        },
        registered_at=90.0,
    )
    return transition_model_version(
        registered,
        "candidate",
        occurred_at=91.0,
        actor="worker",
        reason="evaluate",
    )


def _champion():
    candidate = _candidate("v7")
    return transition_model_version(
        candidate,
        "champion",
        occurred_at=92.0,
        actor="release-manager",
        reason="previous champion",
        promotion_decision={
            "policy_id": "policy-prior",
            "evaluation_id": "eval-prior",
            "evidence_digest": _EVIDENCE,
            "passed": True,
            "evaluated_at": 91.5,
        },
    )


def _evaluation_payload():
    challenger = _candidate("v8")
    return {
        "evaluated_at": 100.0,
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
        "challenger": challenger,
        "champion": _champion(),
        "dataset_digest": _DATASET,
        "window": {"started_at": 80.0, "finished_at": 99.0},
        "observed_artifact_digest": _ARTIFACT,
        "expected_feature_schema_digest": challenger["feature_schema_digest"],
        "observations": [
            {"actual": 1, "candidate_prediction": 0.9, "champion_prediction": 0.7},
            {"actual": 0, "candidate_prediction": 0.1, "champion_prediction": 0.4},
            {"actual": 1, "candidate_prediction": 0.8, "champion_prediction": 0.6},
        ],
    }


def test_capabilities_expose_v431_without_changing_registry_contract():
    response = _client().get("/api/v4.3/capabilities")
    body = response.get_json()
    assert response.status_code == 200
    assert body["version"] == "4.3.2"
    assert body["registry_contract_version"] == "4.3.0"
    assert body["evaluation_contract_version"] == "4.3.1"
    assert body["rollout_contract_version"] == "4.3.2"
    assert body["features"]["automated_evaluation"] is True
    assert body["features"]["champion_challenger_automation"] is True


def test_metric_catalog_endpoint_lists_allowlisted_metrics():
    response = _client().get("/api/v4.3/models/evaluations/metrics")
    body = response.get_json()
    assert response.status_code == 200
    assert len(body["metrics"]) == 6
    assert body["maximum_observations"] == 50_000


def test_evaluation_endpoint_returns_evidence_record():
    response = _client().post(
        "/api/v4.3/models/evaluations/run",
        json=_evaluation_payload(),
    )
    body = response.get_json()
    assert response.status_code == 200
    assert body["contract_version"] == "4.3.1"
    assert body["passed"] is True
    assert body["evaluation_id"].startswith("eval_")
    assert body["metrics"][0]["name"] == "brier-score"


def test_evaluation_endpoint_rejects_unbounded_or_malformed_input():
    response = _client().post(
        "/api/v4.3/models/evaluations/run",
        json={"observations": "not-a-list"},
    )
    assert response.status_code == 400
    assert "policy" in response.get_json()["error"]


def test_selection_endpoint_emits_lifecycle_promotion_decision():
    client = _client()
    evaluation = client.post(
        "/api/v4.3/models/evaluations/run",
        json=_evaluation_payload(),
    ).get_json()
    response = client.post(
        "/api/v4.3/models/champion-challenger/select",
        json={"evaluation": evaluation, "decided_at": 101.0},
    )
    body = response.get_json()
    assert response.status_code == 200
    assert body["action"] == "promote_challenger"
    assert body["promotion_decision"]["passed"] is True
    assert body["promotion_decision"]["evaluation_id"] == evaluation["evaluation_id"]


def test_selection_endpoint_rejects_altered_evidence():
    client = _client()
    evaluation = client.post(
        "/api/v4.3/models/evaluations/run",
        json=_evaluation_payload(),
    ).get_json()
    evaluation["passed"] = False
    response = client.post(
        "/api/v4.3/models/champion-challenger/select",
        json={"evaluation": evaluation, "decided_at": 101.0},
    )
    assert response.status_code == 400
    assert "integrity" in response.get_json()["error"]
