from flask import Flask

from routes.v44_api import v44_bp


def _client():
    app = Flask(__name__)
    app.register_blueprint(v44_bp)
    return app.test_client()


def _organization_payload():
    return {
        "slug": "nfl-labs",
        "name": "NFL Labs",
        "created_by": {"type": "user", "id": "owner@example.com"},
        "created_at": 100.0,
    }


def test_capabilities_preserve_v440_contracts_under_v441_release():
    response = _client().get("/api/v4.4/capabilities")
    body = response.get_json()
    assert response.status_code == 200
    assert body["version"] == "4.4.3"
    assert body["contract_version"] == "4.4.0"
    assert body["features"]["fixed_role_permission_catalog"] is True
    assert body["features"]["api_keys"] is True
    assert body["endpoints"]["access_authorize"].endswith("/access/authorize")


def test_roles_endpoint_exposes_four_immutable_roles():
    response = _client().get("/api/v4.4/access/roles")
    body = response.get_json()
    assert response.status_code == 200
    assert {item["role"] for item in body["roles"]} == {
        "owner",
        "admin",
        "analyst",
        "viewer",
    }


def test_organization_and_membership_routes_return_stable_contracts():
    client = _client()
    organization = client.post(
        "/api/v4.4/organizations/normalize",
        json=_organization_payload(),
    ).get_json()
    response = client.post(
        "/api/v4.4/memberships/normalize",
        json={
            "organization_id": organization["organization_id"],
            "subject": {"type": "user", "id": "analyst@example.com"},
            "role": "analyst",
            "granted_by": {"type": "user", "id": "owner@example.com"},
            "granted_at": 101.0,
        },
    )
    membership = response.get_json()
    assert response.status_code == 200
    assert membership["membership_id"].startswith("membership_")
    assert membership["organization_id"] == organization["organization_id"]


def test_authorization_route_returns_explicit_allow_and_deny_decisions():
    client = _client()
    organization = client.post(
        "/api/v4.4/organizations/normalize",
        json=_organization_payload(),
    ).get_json()
    membership = client.post(
        "/api/v4.4/memberships/normalize",
        json={
            "organization_id": organization["organization_id"],
            "subject": {"type": "user", "id": "viewer@example.com"},
            "role": "viewer",
            "granted_by": {"type": "user", "id": "owner@example.com"},
            "granted_at": 101.0,
        },
    ).get_json()
    request = {
        "organization": organization,
        "memberships": [membership],
        "subject": membership["subject"],
    }
    allowed = client.post(
        "/api/v4.4/access/authorize",
        json={**request, "permission": "decision.read"},
    ).get_json()
    denied = client.post(
        "/api/v4.4/access/authorize",
        json={**request, "permission": "decision.execute"},
    ).get_json()
    assert allowed["allowed"] is True
    assert denied["allowed"] is False
    assert denied["reason"] == "permission-not-granted"


def test_routes_reject_invalid_json_contracts():
    client = _client()
    assert client.post("/api/v4.4/organizations/normalize", json=[]).status_code == 400
    assert client.post("/api/v4.4/memberships/normalize", json={}).status_code == 400
    assert client.post("/api/v4.4/access/authorize", json={}).status_code == 400


def test_v44_blueprint_is_registered_in_integrated_app():
    from app import app

    rules = {rule.rule for rule in app.url_map.iter_rules()}
    assert "/api/v4.4/capabilities" in rules
    assert "/api/v4.3/capabilities" in rules
