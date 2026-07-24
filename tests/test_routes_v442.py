import pytest
from flask import Flask

from database import db
from enterprise_quota_v442 import InMemoryQuotaBackend
from routes.v44_api import v44_bp
from security import configure_security


@pytest.fixture()
def quota_client(monkeypatch):
    monkeypatch.setenv("AUTH_DISABLED", "1")
    app = Flask(__name__)
    app.config.update(
        TESTING=True,
        SECRET_KEY="route-v442-secret",
        SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
    )
    configure_security(app)
    db.init_app(app)
    app.register_blueprint(v44_bp)
    app.extensions["enterprise_quota_backend"] = InMemoryQuotaBackend(
        organization_limit=10,
        credential_limit=5,
        window_seconds=60,
    )
    with app.app_context():
        db.create_all()
        with app.test_client() as client:
            yield client, app
        db.session.remove()
        db.drop_all()


def _organization(client):
    response = client.post(
        "/api/v4.4/directory/organizations",
        json={"slug": "nfl-labs", "name": "NFL Labs"},
    )
    assert response.status_code == 201
    organization = response.get_json()["organization"]
    selected = client.put(
        "/api/v4.4/session/tenant",
        json={"organization_id": organization["organization_id"]},
    )
    assert selected.status_code == 200
    return organization


def _api_key(client, organization_id, scopes=None):
    response = client.post(
        f"/api/v4.4/directory/organizations/{organization_id}/api-keys",
        json={
            "subject": {"type": "user", "id": "developer"},
            "name": "Public decisions",
            "scopes": scopes or ["decision.execute", "organization.read"],
            "expires_in_seconds": 3600,
        },
    )
    assert response.status_code == 201
    return response.get_json()


def _headers(credential, request_id="request-0001"):
    return {
        "X-API-Key": credential,
        "Idempotency-Key": request_id,
    }


def _ensemble(client, credential, request_id="request-0001", probability=0.64):
    return client.post(
        "/api/v4.4/public/decisions/ensemble",
        headers=_headers(credential, request_id),
        json={
            "models": [
                {
                    "name": "calibrated",
                    "probability": probability,
                    "calibration": 0.9,
                    "sample_size": 500,
                }
            ]
        },
    )


def test_capabilities_expose_v442_quota_and_public_api_guarantees(
    quota_client,
):
    client, _ = quota_client
    body = client.get("/api/v4.4/capabilities").get_json()
    assert body["version"] == "4.4.3"
    assert body["contract_version"] == "4.4.0"
    assert body["features"]["redis_usage_accounting"] is True
    assert body["features"]["organization_quotas"] is True
    assert body["features"]["credential_quotas"] is True
    assert body["features"]["idempotent_request_metering"] is True
    assert body["features"]["public_decision_api_key_required"] is True
    assert body["quota_contract"]["fail_closed_without_production_redis"] is True


def test_owner_can_read_and_update_bounded_quota_policy(quota_client):
    client, _ = quota_client
    organization = _organization(client)
    endpoint = f"/api/v4.4/directory/organizations/{organization['organization_id']}/quotas"
    current = client.get(endpoint)
    assert current.status_code == 200
    assert current.get_json()["backend"] == "memory"
    updated = client.put(
        endpoint,
        json={
            "organization_limit": 20,
            "credential_limit": 4,
            "window_seconds": 300,
        },
    )
    assert updated.status_code == 200
    assert updated.get_json()["policy"]["credential_limit"] == 4
    assert client.get(endpoint).get_json()["policy"]["organization_limit"] == 20


def test_quota_policy_rejects_credential_limit_above_organization(quota_client):
    client, _ = quota_client
    organization = _organization(client)
    response = client.put(
        f"/api/v4.4/directory/organizations/{organization['organization_id']}/quotas",
        json={
            "organization_limit": 2,
            "credential_limit": 3,
            "window_seconds": 60,
        },
    )
    assert response.status_code == 400
    assert "cannot exceed" in response.get_json()["error"]


def test_public_ensemble_requires_api_key_even_with_tenant_session(quota_client):
    client, _ = quota_client
    _organization(client)
    response = client.post(
        "/api/v4.4/public/decisions/ensemble",
        headers={"Idempotency-Key": "request-0001"},
        json={"models": [{"probability": 0.6}]},
    )
    assert response.status_code == 401
    assert response.get_json()["code"] == "API_KEY_REQUIRED"


def test_public_ensemble_returns_stable_envelope_and_quota_headers(
    quota_client,
):
    client, _ = quota_client
    organization = _organization(client)
    issued = _api_key(client, organization["organization_id"])
    response = _ensemble(client, issued["credential"])
    body = response.get_json()
    assert response.status_code == 200
    assert body["version"] == "4.4.2"
    assert body["operation"] == "decision.ensemble"
    assert body["result"]["decision"] == "home"
    assert body["quota"]["organization"]["used"] == 1
    assert response.headers["RateLimit-Remaining"] == "4"
    assert response.headers["X-RateLimit-Organization-Remaining"] == "9"


def test_exact_public_replay_is_not_charged_twice(quota_client):
    client, _ = quota_client
    organization = _organization(client)
    issued = _api_key(client, organization["organization_id"])
    first = _ensemble(client, issued["credential"])
    replay = _ensemble(client, issued["credential"])
    assert first.get_json()["quota"]["credential"]["used"] == 1
    assert replay.get_json()["quota"]["credential"]["used"] == 1
    assert replay.get_json()["quota"]["replayed"] is True
    assert replay.headers["Idempotent-Replay"] == "true"


def test_idempotency_key_reuse_with_different_payload_conflicts(quota_client):
    client, _ = quota_client
    organization = _organization(client)
    issued = _api_key(client, organization["organization_id"])
    _ensemble(client, issued["credential"], probability=0.6)
    response = _ensemble(client, issued["credential"], probability=0.8)
    assert response.status_code == 409
    assert response.get_json()["code"] == "IDEMPOTENCY_CONFLICT"


def test_quota_exhaustion_returns_inspectable_429(quota_client):
    client, _ = quota_client
    organization = _organization(client)
    endpoint = f"/api/v4.4/directory/organizations/{organization['organization_id']}/quotas"
    client.put(
        endpoint,
        json={
            "organization_limit": 1,
            "credential_limit": 1,
            "window_seconds": 60,
        },
    )
    issued = _api_key(client, organization["organization_id"])
    assert _ensemble(client, issued["credential"], "request-0001").status_code == 200
    response = _ensemble(client, issued["credential"], "request-0002")
    body = response.get_json()
    assert response.status_code == 429
    assert body["code"] == "QUOTA_EXCEEDED"
    assert body["quota"]["exceeded_scope"] == "organization"
    assert body["quota"]["organization"]["remaining"] == 0
    assert int(response.headers["Retry-After"]) >= 1
    assert response.headers["X-RateLimit-Exceeded-Scope"] == "organization"


def test_usage_endpoint_reports_organization_and_credential_counts(
    quota_client,
):
    client, _ = quota_client
    organization = _organization(client)
    issued = _api_key(client, organization["organization_id"])
    _ensemble(client, issued["credential"])
    response = client.get(
        f"/api/v4.4/directory/organizations/{organization['organization_id']}/usage",
        query_string={"api_key_id": issued["api_key_id"]},
    )
    body = response.get_json()
    assert response.status_code == 200
    assert body["backend"] == "memory"
    assert body["organization"]["used"] == 1
    assert body["credential"]["used"] == 1


def test_public_scenario_and_brief_use_the_same_meter_contract(quota_client):
    client, _ = quota_client
    organization = _organization(client)
    issued = _api_key(client, organization["organization_id"])
    scenario = client.post(
        "/api/v4.4/public/decisions/scenario",
        headers=_headers(issued["credential"], "scenario-0001"),
        json={
            "baseline": {"probability": 0.55},
            "scenarios": [{"name": "weather", "probability_delta": -0.03}],
        },
    )
    brief = client.post(
        "/api/v4.4/public/decisions/brief",
        headers=_headers(issued["credential"], "brief-request-01"),
        json={
            "ensemble": {
                "probability": 0.55,
                "confidence": 0.8,
                "disagreement": 0.05,
                "primary_model": "calibrated",
            },
            "scenario": scenario.get_json()["result"],
        },
    )
    assert scenario.status_code == 200
    assert scenario.get_json()["operation"] == "decision.scenario"
    assert brief.status_code == 200
    assert brief.get_json()["operation"] == "decision.brief"
    assert brief.get_json()["result"]["grounded"] is True


def test_public_decision_requires_decision_execute_scope(quota_client):
    client, _ = quota_client
    organization = _organization(client)
    issued = _api_key(
        client,
        organization["organization_id"],
        scopes=["organization.read"],
    )
    response = _ensemble(client, issued["credential"])
    assert response.status_code == 403
    assert response.get_json()["code"] == "ENTERPRISE_ACCESS_DENIED"


def test_missing_idempotency_key_is_rejected_before_execution(quota_client):
    client, _ = quota_client
    organization = _organization(client)
    issued = _api_key(client, organization["organization_id"])
    response = client.post(
        "/api/v4.4/public/decisions/ensemble",
        headers={"X-API-Key": issued["credential"]},
        json={"models": [{"probability": 0.6}]},
    )
    assert response.status_code == 400
    assert response.get_json()["code"] == "INVALID_REQUEST"


def test_public_payloads_are_bounded(quota_client):
    client, _ = quota_client
    organization = _organization(client)
    issued = _api_key(client, organization["organization_id"])
    response = client.post(
        "/api/v4.4/public/decisions/ensemble",
        headers=_headers(issued["credential"]),
        json={"models": [{"probability": 0.5}] * 101},
    )
    assert response.status_code == 400
    assert "more than 100" in response.get_json()["error"]


def test_quota_backend_failure_returns_retryable_503(quota_client):
    client, app = quota_client
    organization = _organization(client)
    issued = _api_key(client, organization["organization_id"])

    class UnavailableBackend:
        def consume(self, *args, **kwargs):
            raise RuntimeError("enterprise quota Redis backend is unavailable")

    app.extensions["enterprise_quota_backend"] = UnavailableBackend()
    response = _ensemble(client, issued["credential"])
    assert response.status_code == 503
    assert response.get_json() == {
        "error": "enterprise quota Redis backend is unavailable",
        "code": "QUOTA_BACKEND_UNAVAILABLE",
        "retryable": True,
    }


def test_integrated_app_registers_v442_public_routes():
    from app import app

    rules = {rule.rule for rule in app.url_map.iter_rules()}
    assert "/api/v4.4/public/decisions/ensemble" in rules
    assert "/api/v4.4/public/decisions/scenario" in rules
    assert "/api/v4.4/public/decisions/brief" in rules
    assert "/api/v4/decisions/ensemble" in rules
