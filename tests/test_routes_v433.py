from flask import Flask

from operations_v433 import InMemoryLifecycleOperations
from routes.v43_api import v43_bp

_ARTIFACT = "a" * 64
_EVIDENCE = "e" * 64


def _client():
    app = Flask(__name__)
    app.config["SECRET_KEY"] = "test"
    app.extensions["v43_lifecycle_operations"] = InMemoryLifecycleOperations()
    app.register_blueprint(v43_bp)
    return app.test_client()


def _model():
    return {
        "model_key": "win-probability",
        "version": "v8",
        "target": "home-win",
        "algorithm": "ensemble",
        "feature_schema": [{"name": "epa", "data_type": "number"}],
        "artifact": {"uri": "s3://models/v8.bin", "digest": _ARTIFACT},
        "registered_by": "trainer",
        "registered_at": 100.0,
    }


def test_capabilities_expose_v433_operations_and_workspace():
    response = _client().get("/api/v4.3/capabilities")
    body = response.get_json()
    assert response.status_code == 200
    assert body["version"] == "4.3.3"
    assert body["features"]["persistent_registry_adapters"] is True
    assert body["features"]["operator_workspace"] is True
    assert body["operations_contract_version"] == "4.3.3"


def test_registry_routes_persist_list_and_read_versions():
    client = _client()
    created = client.post("/api/v4.3/operations/registry/versions", json=_model())
    model = created.get_json()["model_version"]
    assert created.status_code == 201
    listed = client.get("/api/v4.3/operations/registry/versions").get_json()
    assert listed["count"] == 1
    fetched = client.get(f"/api/v4.3/operations/registry/versions/{model['model_version_id']}")
    assert fetched.get_json()["metadata_digest"] == model["metadata_digest"]


def test_registry_route_returns_conflict_and_not_found_errors():
    client = _client()
    client.post("/api/v4.3/operations/registry/versions", json=_model())
    conflicting = client.post(
        "/api/v4.3/operations/registry/versions",
        json={**_model(), "algorithm": "other"},
    )
    assert conflicting.status_code == 400
    missing = client.get("/api/v4.3/operations/registry/versions/mv_1234567890abcdef1234")
    assert missing.status_code == 404


def test_controlled_transition_route_enforces_four_eyes_approval():
    client = _client()
    model = client.post(
        "/api/v4.3/operations/registry/versions",
        json=_model(),
    ).get_json()["model_version"]
    candidate = client.post(
        f"/api/v4.3/operations/registry/versions/{model['model_version_id']}/transitions",
        json={
            "target_status": "candidate",
            "occurred_at": 101.0,
            "actor": "trainer",
            "reason": "evaluate",
        },
    ).get_json()
    blocked = client.post(
        f"/api/v4.3/operations/registry/versions/{model['model_version_id']}/transitions",
        json={
            "target_status": "champion",
            "occurred_at": 105.0,
            "actor": "release-manager",
            "reason": "promote",
            "promotion_decision": {
                "policy_id": "policy-main",
                "evaluation_id": "eval-main",
                "evidence_digest": _EVIDENCE,
                "passed": True,
                "evaluated_at": 102.0,
            },
        },
    )
    assert blocked.status_code == 400
    requested = client.post(
        "/api/v4.3/operations/approvals",
        json={
            "action": "model.lifecycle.transition",
            "target_status": "champion",
            "resource_id": candidate["model_version_id"],
            "evidence_digest": _EVIDENCE,
            "requested_by": "release-manager",
            "reason": "promote challenger",
            "requested_at": 103.0,
            "expires_at": 200.0,
        },
    ).get_json()["approval"]
    approved = client.post(
        f"/api/v4.3/operations/approvals/{requested['approval_id']}/decisions",
        json={
            "decision": "approved",
            "actor": "risk-owner",
            "reason": "evidence verified",
            "decided_at": 104.0,
        },
    ).get_json()
    promoted = client.post(
        f"/api/v4.3/operations/registry/versions/{model['model_version_id']}/transitions",
        json={
            "target_status": "champion",
            "occurred_at": 105.0,
            "actor": "release-manager",
            "reason": "approved promotion",
            "approval_id": approved["approval_id"],
            "promotion_decision": {
                "policy_id": "policy-main",
                "evaluation_id": "eval-main",
                "evidence_digest": _EVIDENCE,
                "passed": True,
                "evaluated_at": 102.0,
            },
        },
    )
    assert promoted.status_code == 200
    assert promoted.get_json()["status"] == "champion"


def test_approval_routes_list_pending_and_reject_self_approval():
    client = _client()
    request = client.post(
        "/api/v4.3/operations/approvals",
        json={
            "action": "model.rollout.advance",
            "resource_id": "rollout_main",
            "evidence_digest": _EVIDENCE,
            "requested_by": "operator-a",
            "reason": "advance canary",
            "requested_at": 100.0,
            "expires_at": 200.0,
        },
    ).get_json()["approval"]
    pending = client.get("/api/v4.3/operations/approvals?status=pending").get_json()
    assert pending["count"] == 1
    rejected = client.post(
        f"/api/v4.3/operations/approvals/{request['approval_id']}/decisions",
        json={
            "decision": "approved",
            "actor": "operator-a",
            "reason": "self approval",
            "decided_at": 101.0,
        },
    )
    assert rejected.status_code == 400
    assert "differ" in rejected.get_json()["error"]


def test_health_status_and_audit_routes_expose_real_state():
    client = _client()
    model = client.post(
        "/api/v4.3/operations/registry/versions",
        json=_model(),
    ).get_json()["model_version"]
    health = client.post(
        "/api/v4.3/operations/health/observations",
        json={
            "model_version_id": model["model_version_id"],
            "observed_at": 101.0,
            "actor": "observer",
            "checks": [
                {
                    "name": "quality",
                    "healthy": False,
                    "severity": "critical",
                    "observed_at": 101.0,
                    "evidence_digest": _EVIDENCE,
                    "detail": "quality breach",
                }
            ],
        },
    )
    assert health.status_code == 201
    assert health.get_json()["alerts"][0]["severity"] == "critical"
    status = client.get("/api/v4.3/operations/status").get_json()
    assert status["registry"]["total"] == 1
    assert status["health"]["unhealthy_models"] == 1
    audit = client.get("/api/v4.3/operations/audit").get_json()
    assert audit["count"] == 2


def test_workspace_manifest_discloses_safety_boundaries():
    response = _client().get("/api/v4.3/operations/workspace")
    body = response.get_json()
    assert response.status_code == 200
    assert body["route"] == "/model-operations"
    assert {item["id"] for item in body["panels"]} == {
        "registry",
        "approvals",
        "health",
        "audit",
    }
    assert any("deploys" in item for item in body["guardrails"])


def test_routes_reject_invalid_operational_contracts():
    client = _client()
    assert client.post("/api/v4.3/operations/registry/versions", json=[]).status_code == 400
    assert client.post("/api/v4.3/operations/approvals", json={}).status_code == 400
    assert (
        client.post(
            "/api/v4.3/operations/health/observations",
            json={"model_version_id": "bad", "checks": []},
        ).status_code
        == 400
    )
