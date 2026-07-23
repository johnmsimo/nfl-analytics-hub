from flask import Flask

from routes.v43_api import v43_bp

_DIGEST = "a" * 64


def _client():
    app = Flask(__name__)
    app.register_blueprint(v43_bp)
    return app.test_client()


def _model():
    return {
        "model_key": "game-win-probability",
        "version": "v8",
        "target": "home-win",
        "algorithm": "calibrated-ensemble",
        "feature_schema": [
            {
                "name": "home-epa",
                "data_type": "number",
                "source": "warehouse",
            }
        ],
        "artifact": {
            "uri": "s3://models/game-win-probability/v8.bin",
            "digest": _DIGEST,
        },
        "registered_at": 100.0,
    }


def test_capabilities_expose_model_lifecycle_foundation():
    response = _client().get("/api/v4.3/capabilities")
    body = response.get_json()
    assert response.status_code == 200
    assert body["version"] == "4.3.1"
    assert body["features"]["conflict_safe_registration"] is True
    assert body["features"]["champion_challenger_automation"] is True
    assert body["registry_contract_version"] == "4.3.0"


def test_model_version_endpoint_returns_stable_registry_contract():
    response = _client().post("/api/v4.3/models/versions/normalize", json=_model())
    body = response.get_json()
    assert response.status_code == 200
    assert body["model_version_id"].startswith("mv_")
    assert body["artifact"]["digest"] == f"sha256:{_DIGEST}"
    assert body["status"] == "registered"


def test_model_version_endpoint_rejects_invalid_feature_schema():
    payload = _model()
    payload["feature_schema"] = [{"name": "home-epa", "data_type": "mystery"}]
    response = _client().post("/api/v4.3/models/versions/normalize", json=payload)
    assert response.status_code == 400
    assert "data_type" in response.get_json()["error"]


def test_transition_endpoint_validates_candidate_flow():
    model = _client().post("/api/v4.3/models/versions/normalize", json=_model()).get_json()
    response = _client().post(
        "/api/v4.3/models/transitions/validate",
        json={
            "model_version": model,
            "target_status": "candidate",
            "occurred_at": 101.0,
            "actor": "release-manager",
            "reason": "ready for evaluation",
        },
    )
    body = response.get_json()
    assert response.status_code == 200
    assert body["status"] == "candidate"
    assert body["history"][0]["event_type"] == "model.lifecycle.transitioned"


def test_transition_endpoint_rejects_unevaluated_champion():
    model = _client().post("/api/v4.3/models/versions/normalize", json=_model()).get_json()
    response = _client().post(
        "/api/v4.3/models/transitions/validate",
        json={
            "model_version": model,
            "target_status": "champion",
            "occurred_at": 101.0,
            "actor": "release-manager",
            "reason": "skip evaluation",
        },
    )
    assert response.status_code == 400
    assert "cannot transition" in response.get_json()["error"]


def test_promotion_policy_endpoint_returns_bounded_contract():
    response = _client().post(
        "/api/v4.3/models/promotion-policies/normalize",
        json={
            "model_key": "game-win-probability",
            "target": "home-win",
            "minimum_samples": 500,
            "metrics": [
                {
                    "name": "brier-score",
                    "direction": "lower",
                    "threshold": 0.2,
                    "minimum_improvement": 0.01,
                }
            ],
        },
    )
    body = response.get_json()
    assert response.status_code == 200
    assert body["policy_id"].startswith("policy_")
    assert body["metrics"][0]["direction"] == "lower"
