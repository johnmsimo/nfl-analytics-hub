import os

import pytest
from flask import Flask

from database import db
from db_models import EnterpriseApiKey
from routes.v44_api import v44_bp
from security import configure_security


@pytest.fixture()
def enterprise_client(monkeypatch):
    monkeypatch.setenv("AUTH_DISABLED", "1")
    app = Flask(__name__)
    app.config.update(
        TESTING=True,
        SECRET_KEY="route-test-secret",
        SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
    )
    configure_security(app)
    db.init_app(app)
    app.register_blueprint(v44_bp)

    @app.get("/api/other")
    def other_api():
        return {"ok": True}

    with app.app_context():
        db.create_all()
        with app.test_client() as client:
            yield client, app
        db.session.remove()
        db.drop_all()


def _persist_organization(client):
    response = client.post(
        "/api/v4.4/directory/organizations",
        json={"slug": "nfl-labs", "name": "NFL Labs"},
    )
    assert response.status_code == 201
    return response.get_json()["organization"]


def _select_tenant(client, organization_id):
    response = client.put(
        "/api/v4.4/session/tenant",
        json={"organization_id": organization_id},
    )
    assert response.status_code == 200
    return response.get_json()["tenant"]


def test_capabilities_expose_v441_persistence_and_key_guarantees(
    enterprise_client,
):
    client, _ = enterprise_client
    body = client.get("/api/v4.4/capabilities").get_json()
    assert body["version"] == "4.4.3"
    assert body["contract_version"] == "4.4.0"
    assert body["features"]["persistent_tenant_directory"] is True
    assert body["features"]["enterprise_route_tenant_enforcement"] is True
    assert body["features"]["runtime_tenant_enforcement"] is False
    assert body["features"]["api_keys"] is True
    assert body["features"]["api_key_plaintext_storage"] is False


def test_organization_bootstrap_and_tenant_session_round_trip(
    enterprise_client,
):
    client, _ = enterprise_client
    organization = _persist_organization(client)
    tenant = _select_tenant(client, organization["organization_id"])
    assert tenant["role"] == "owner"
    selected = client.get("/api/v4.4/session/tenant").get_json()
    assert selected["tenant"]["organization_id"] == organization["organization_id"]
    cleared = client.delete("/api/v4.4/session/tenant").get_json()
    assert cleared["cleared"] is True


def test_owner_can_persist_service_membership(enterprise_client):
    client, _ = enterprise_client
    organization = _persist_organization(client)
    _select_tenant(client, organization["organization_id"])
    response = client.post(
        (f"/api/v4.4/directory/organizations/{organization['organization_id']}/memberships"),
        json={
            "subject": {"type": "service", "id": "prediction-worker"},
            "role": "analyst",
        },
    )
    assert response.status_code == 201
    assert response.get_json()["membership"]["subject"]["type"] == "service"


def test_key_issue_list_and_storage_redaction(enterprise_client):
    client, app = enterprise_client
    organization = _persist_organization(client)
    _select_tenant(client, organization["organization_id"])
    response = client.post(
        (f"/api/v4.4/directory/organizations/{organization['organization_id']}/api-keys"),
        json={
            "subject": {"type": "user", "id": "developer"},
            "name": "Owner automation",
            "scopes": ["api-key.manage", "organization.read"],
            "expires_in_seconds": 3600,
        },
    )
    issued = response.get_json()
    assert response.status_code == 201
    assert issued["credential_visible_once"] is True
    listed = client.get(
        f"/api/v4.4/directory/organizations/{organization['organization_id']}/api-keys"
    ).get_json()["api_keys"]
    assert "credential" not in listed[0]
    with app.app_context():
        record = db.session.get(EnterpriseApiKey, issued["api_key_id"])
        assert issued["credential"] not in record.secret_digest


def test_api_key_authenticates_v44_and_cannot_escape_route_boundary(
    enterprise_client,
):
    client, _ = enterprise_client
    organization = _persist_organization(client)
    _select_tenant(client, organization["organization_id"])
    issued = client.post(
        (f"/api/v4.4/directory/organizations/{organization['organization_id']}/api-keys"),
        json={
            "subject": {"type": "user", "id": "developer"},
            "name": "Owner automation",
            "scopes": ["api-key.manage", "organization.read"],
            "expires_in_seconds": 3600,
        },
    ).get_json()
    client.delete("/api/v4.4/session/tenant")
    headers = {"X-API-Key": issued["credential"]}
    tenant = client.get(
        "/api/v4.4/session/tenant",
        headers=headers,
    ).get_json()["tenant"]
    assert tenant["authentication"] == "api_key"
    listed = client.get(
        (f"/api/v4.4/directory/organizations/{organization['organization_id']}/api-keys"),
        headers=headers,
    )
    assert listed.status_code == 200
    escaped = client.get("/api/other", headers=headers)
    assert escaped.status_code == 401
    assert escaped.get_json()["code"] == "API_KEY_ROUTE_UNSUPPORTED"


def test_invalid_api_key_fails_closed(enterprise_client):
    client, _ = enterprise_client
    response = client.get(
        "/api/v4.4/session/tenant",
        headers={"X-API-Key": "nfl_v441_bad"},
    )
    assert response.status_code == 401
    assert response.get_json()["code"] == "INVALID_API_KEY"


def test_api_key_scope_is_enforced_by_management_route(enterprise_client):
    client, _ = enterprise_client
    organization = _persist_organization(client)
    _select_tenant(client, organization["organization_id"])
    issued = client.post(
        (f"/api/v4.4/directory/organizations/{organization['organization_id']}/api-keys"),
        json={
            "subject": {"type": "user", "id": "developer"},
            "name": "Read only",
            "scopes": ["organization.read"],
            "expires_in_seconds": 3600,
        },
    ).get_json()
    response = client.get(
        (f"/api/v4.4/directory/organizations/{organization['organization_id']}/api-keys"),
        headers={"X-API-Key": issued["credential"]},
    )
    assert response.status_code == 403
    assert response.get_json()["code"] == "ENTERPRISE_ACCESS_DENIED"


def test_rotation_and_revocation_routes_invalidate_old_credentials(
    enterprise_client,
):
    client, _ = enterprise_client
    organization = _persist_organization(client)
    _select_tenant(client, organization["organization_id"])
    base = f"/api/v4.4/directory/organizations/{organization['organization_id']}/api-keys"
    issued = client.post(
        base,
        json={
            "subject": {"type": "user", "id": "developer"},
            "name": "Rotating",
            "scopes": ["api-key.manage", "organization.read"],
            "expires_in_seconds": 3600,
        },
    ).get_json()
    rotated = client.post(
        f"{base}/{issued['api_key_id']}/rotate",
        json={"expires_in_seconds": 7200},
    ).get_json()["replacement"]
    assert rotated["rotated_from_id"] == issued["api_key_id"]
    assert (
        client.get(
            "/api/v4.4/session/tenant",
            headers={"X-API-Key": issued["credential"]},
        ).status_code
        == 401
    )
    assert client.post(f"{base}/{rotated['api_key_id']}/revoke").get_json()["revoked"] is True
    assert (
        client.get(
            "/api/v4.4/session/tenant",
            headers={"X-API-Key": rotated["credential"]},
        ).status_code
        == 401
    )


def test_session_endpoint_exposes_tenant_context_in_integrated_auth_shape(
    enterprise_client,
):
    client, _ = enterprise_client
    organization = _persist_organization(client)
    _select_tenant(client, organization["organization_id"])
    with client.session_transaction() as current:
        assert current["enterprise_tenant"]["organization_id"] == organization["organization_id"]


def test_production_requires_explicit_auth_not_disabled(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("AUTH_DISABLED", "1")
    monkeypatch.setenv("SECRET_KEY", "production-test")
    app = Flask(__name__)
    configure_security(app)
    app.config.update(
        TESTING=True,
        SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
    )
    db.init_app(app)
    app.register_blueprint(v44_bp)
    with app.app_context():
        db.create_all()
        with app.test_client() as client:
            response = client.post(
                "/api/v4.4/directory/organizations",
                json={"slug": "nfl-labs", "name": "NFL Labs"},
            )
            assert response.status_code == 401
        db.session.remove()
        db.drop_all()
    os.environ.pop("APP_ENV", None)
