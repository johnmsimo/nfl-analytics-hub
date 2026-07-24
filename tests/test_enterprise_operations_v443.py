from datetime import UTC, datetime, timedelta

import pytest
from flask import Flask

from database import db
from db_models import (
    EnterpriseAuditEvent,
    EnterpriseMembership,
    EnterpriseReport,
    EnterpriseSavedDecision,
)
from routes.v44_api import v44_bp
from security import configure_security


@pytest.fixture()
def operations_client(monkeypatch):
    monkeypatch.setenv("AUTH_DISABLED", "1")
    app = Flask(__name__)
    app.config.update(
        TESTING=True,
        SECRET_KEY="operations-test-secret",
        SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
    )
    configure_security(app)
    db.init_app(app)
    app.register_blueprint(v44_bp)
    with app.app_context():
        db.create_all()
        with app.test_client() as client:
            yield client, app
        db.session.remove()
        db.drop_all()


def _organization(client, slug="nfl-labs"):
    response = client.post(
        "/api/v4.4/directory/organizations",
        json={"slug": slug, "name": slug.replace("-", " ").title()},
    )
    assert response.status_code == 201
    organization = response.get_json()["organization"]
    selected = client.put(
        "/api/v4.4/session/tenant",
        json={"organization_id": organization["organization_id"]},
    )
    assert selected.status_code == 200
    return organization


def _workspace(client, organization_id, slug="weekly-scouting"):
    response = client.post(
        f"/api/v4.4/directory/organizations/{organization_id}/workspaces",
        json={
            "slug": slug,
            "name": slug.replace("-", " ").title(),
            "description": "Shared evidence-backed decisions.",
        },
    )
    assert response.status_code == 201
    return response.get_json()["workspace"]


def _membership(client, organization_id, subject_id, role="analyst"):
    response = client.post(
        f"/api/v4.4/directory/organizations/{organization_id}/memberships",
        json={
            "subject": {"type": "user", "id": subject_id},
            "role": role,
        },
    )
    assert response.status_code == 201
    return response.get_json()["membership"]


def _switch_user(client, organization_id, username):
    with client.session_transaction() as current:
        current["user"] = {"username": username, "name": username}
        current.pop("enterprise_tenant", None)
    response = client.put(
        "/api/v4.4/session/tenant",
        json={"organization_id": organization_id},
    )
    assert response.status_code == 200
    return response.get_json()["tenant"]


def _decision(client, organization_id, workspace_id):
    response = client.post(
        (f"/api/v4.4/directory/organizations/{organization_id}/workspaces/{workspace_id}/decisions"),
        json={
            "operation": "decision.ensemble",
            "title": "Week 1 matchup",
            "tags": ["week-1"],
            "payload": {
                "decision": "home",
                "probability": 0.62,
                "confidence": 0.81,
            },
        },
    )
    assert response.status_code == 201
    return response.get_json()["decision"]


def test_capabilities_complete_v443_contract(operations_client):
    client, _ = operations_client
    body = client.get("/api/v4.4/capabilities").get_json()
    assert body["version"] == "4.4.3"
    assert body["features"]["shared_workspaces"] is True
    assert body["features"]["append_only_enterprise_audit"] is True
    assert body["features"]["retention_hard_delete"] is False
    assert body["operations_contract"]["features"]["hash_linked_audit_integrity"] is True


def test_workspace_creation_is_tenant_scoped_and_conflict_safe(
    operations_client,
):
    client, _ = operations_client
    organization = _organization(client)
    workspace = _workspace(client, organization["organization_id"])
    endpoint = f"/api/v4.4/directory/organizations/{organization['organization_id']}/workspaces"
    replay = client.post(
        endpoint,
        json={
            "slug": "weekly-scouting",
            "name": "Weekly Scouting",
            "description": "Shared evidence-backed decisions.",
        },
    )
    conflict = client.post(
        endpoint,
        json={"slug": "weekly-scouting", "name": "Different"},
    )
    assert replay.status_code == 200
    assert replay.get_json()["deduplicated"] is True
    assert conflict.status_code == 400
    assert workspace["organization_id"] == organization["organization_id"]


def test_cross_tenant_workspace_access_fails_closed(operations_client):
    client, _ = operations_client
    first = _organization(client, "first-tenant")
    workspace = _workspace(client, first["organization_id"])
    second = _organization(client, "second-tenant")
    denied = client.get(f"/api/v4.4/directory/organizations/{first['organization_id']}/workspaces")
    hidden = client.get(
        f"/api/v4.4/directory/organizations/{second['organization_id']}/"
        f"workspaces/{workspace['workspace_id']}/decisions"
    )
    assert denied.status_code == 403
    assert hidden.status_code == 404


def test_collaborator_acl_narrows_organization_role(operations_client):
    client, _ = operations_client
    organization = _organization(client)
    workspace = _workspace(client, organization["organization_id"])
    analyst = _membership(
        client,
        organization["organization_id"],
        "analyst@example.com",
    )
    endpoint = (
        f"/api/v4.4/directory/organizations/{organization['organization_id']}/"
        f"workspaces/{workspace['workspace_id']}/collaborators"
    )
    assert (
        client.put(
            endpoint,
            json={
                "membership_id": analyst["membership_id"],
                "access_level": "viewer",
            },
        ).status_code
        == 201
    )
    _switch_user(
        client,
        organization["organization_id"],
        "analyst@example.com",
    )
    decisions = endpoint.replace("/collaborators", "/decisions")
    assert client.get(decisions).status_code == 200
    denied = client.post(
        decisions,
        json={
            "operation": "decision.ensemble",
            "title": "Denied write",
            "payload": {"decision": "home"},
        },
    )
    assert denied.status_code == 403
    _switch_user(client, organization["organization_id"], "developer")
    client.put(
        endpoint,
        json={
            "membership_id": analyst["membership_id"],
            "access_level": "editor",
        },
    )
    _switch_user(
        client,
        organization["organization_id"],
        "analyst@example.com",
    )
    allowed = client.post(
        decisions,
        json={
            "operation": "decision.ensemble",
            "title": "Allowed write",
            "payload": {"decision": "home"},
        },
    )
    assert allowed.status_code == 201


def test_inactive_membership_loses_workspace_access(operations_client):
    client, app = operations_client
    organization = _organization(client)
    workspace = _workspace(client, organization["organization_id"])
    analyst = _membership(
        client,
        organization["organization_id"],
        "analyst@example.com",
    )
    collaborator_endpoint = (
        f"/api/v4.4/directory/organizations/{organization['organization_id']}/"
        f"workspaces/{workspace['workspace_id']}/collaborators"
    )
    client.put(
        collaborator_endpoint,
        json={
            "membership_id": analyst["membership_id"],
            "access_level": "viewer",
        },
    )
    with app.app_context():
        record = db.session.get(EnterpriseMembership, analyst["membership_id"])
        record.status = "suspended"
        db.session.commit()
    with client.session_transaction() as current:
        current["user"] = {
            "username": "analyst@example.com",
            "name": "Analyst",
        }
        current.pop("enterprise_tenant", None)
    denied = client.put(
        "/api/v4.4/session/tenant",
        json={"organization_id": organization["organization_id"]},
    )
    assert denied.status_code == 403


def test_saved_decision_and_report_integrity_is_verified(operations_client):
    client, app = operations_client
    organization = _organization(client)
    workspace = _workspace(client, organization["organization_id"])
    decision = _decision(
        client,
        organization["organization_id"],
        workspace["workspace_id"],
    )
    reports_endpoint = (
        f"/api/v4.4/directory/organizations/{organization['organization_id']}/"
        f"workspaces/{workspace['workspace_id']}/reports"
    )
    report = client.post(
        reports_endpoint,
        json={
            "title": "Week 1 report",
            "status": "published",
            "decision_ids": [decision["decision_id"]],
            "content": {"summary": "Home side supported by the ensemble."},
        },
    )
    assert report.status_code == 201
    with app.app_context():
        stored = db.session.get(
            EnterpriseSavedDecision,
            decision["decision_id"],
        )
        stored.payload = {"decision": "away"}
        db.session.commit()
    decisions_endpoint = reports_endpoint.replace("/reports", "/decisions")
    tampered = client.get(decisions_endpoint)
    assert tampered.status_code == 400
    assert "payload_digest" in tampered.get_json()["error"]


def test_saved_content_size_limits_fail_before_persistence(operations_client):
    client, app = operations_client
    organization = _organization(client)
    workspace = _workspace(client, organization["organization_id"])
    response = client.post(
        (
            f"/api/v4.4/directory/organizations/{organization['organization_id']}/"
            f"workspaces/{workspace['workspace_id']}/decisions"
        ),
        json={
            "operation": "decision.ensemble",
            "title": "Oversized",
            "payload": {"blob": "x" * (129 * 1024)},
        },
    )
    assert response.status_code == 400
    assert "exceeds" in response.get_json()["error"]
    with app.app_context():
        assert EnterpriseSavedDecision.query.count() == 0


def test_report_rejects_cross_workspace_decisions(operations_client):
    client, _ = operations_client
    organization = _organization(client)
    first = _workspace(client, organization["organization_id"], "workspace-one")
    second = _workspace(client, organization["organization_id"], "workspace-two")
    decision = _decision(
        client,
        organization["organization_id"],
        first["workspace_id"],
    )
    response = client.post(
        (
            f"/api/v4.4/directory/organizations/{organization['organization_id']}/"
            f"workspaces/{second['workspace_id']}/reports"
        ),
        json={
            "title": "Cross workspace",
            "content": {"summary": "Invalid"},
            "decision_ids": [decision["decision_id"]],
        },
    )
    assert response.status_code == 400
    assert "same workspace" in response.get_json()["error"]


def test_append_only_audit_chain_detects_tampering(operations_client):
    client, app = operations_client
    organization = _organization(client)
    _workspace(client, organization["organization_id"])
    endpoint = f"/api/v4.4/directory/organizations/{organization['organization_id']}/audit"
    valid = client.get(endpoint).get_json()
    assert valid["append_only"] is True
    assert valid["chain_valid"] is True
    assert valid["events"][0]["action"] == "workspace.created"
    with app.app_context():
        event = EnterpriseAuditEvent.query.first()
        event.metadata_json = {"tampered": True}
        db.session.commit()
    invalid = client.get(endpoint).get_json()
    assert invalid["chain_valid"] is False
    assert invalid["head_digest"] is None


def test_retention_redacts_content_without_deleting_metadata(
    operations_client,
):
    client, app = operations_client
    organization = _organization(client)
    workspace = _workspace(client, organization["organization_id"])
    decision = _decision(
        client,
        organization["organization_id"],
        workspace["workspace_id"],
    )
    report = client.post(
        (
            f"/api/v4.4/directory/organizations/{organization['organization_id']}/"
            f"workspaces/{workspace['workspace_id']}/reports"
        ),
        json={
            "title": "Expiring report",
            "content": {"summary": "Retained"},
            "decision_ids": [decision["decision_id"]],
        },
    ).get_json()["report"]
    with app.app_context():
        past = datetime.now(UTC) - timedelta(seconds=1)
        db.session.get(
            EnterpriseSavedDecision,
            decision["decision_id"],
        ).retained_until = past
        db.session.get(EnterpriseReport, report["report_id"]).retained_until = past
        db.session.commit()
    applied = client.post(
        f"/api/v4.4/directory/organizations/{organization['organization_id']}/retention/apply"
    ).get_json()
    assert applied["expired_decisions"] == 1
    assert applied["expired_reports"] == 1
    assert applied["content_hard_deleted"] is False
    with app.app_context():
        stored = db.session.get(
            EnterpriseSavedDecision,
            decision["decision_id"],
        )
        assert stored is not None
        assert stored.payload is None
        assert stored.payload_digest == decision["payload_digest"]


def test_export_policy_and_audit_inclusion_are_enforced(operations_client):
    client, _ = operations_client
    organization = _organization(client)
    workspace = _workspace(client, organization["organization_id"])
    _decision(
        client,
        organization["organization_id"],
        workspace["workspace_id"],
    )
    base = f"/api/v4.4/directory/organizations/{organization['organization_id']}"
    exported = client.post(
        f"{base}/exports",
        json={
            "workspace_id": workspace["workspace_id"],
            "include_audit": True,
        },
    )
    assert exported.status_code == 200
    assert exported.get_json()["audit"]["chain_valid"] is True
    assert exported.headers["Content-Disposition"].endswith('.json"')
    client.put(
        f"{base}/retention",
        json={
            "decision_days": 30,
            "report_days": 60,
            "export_enabled": False,
        },
    )
    disabled = client.post(f"{base}/exports", json={"include_audit": False})
    assert disabled.status_code == 403
    assert "disabled" in disabled.get_json()["error"]


def test_scoped_api_key_cannot_write_without_workspace_permission(
    operations_client,
):
    client, _ = operations_client
    organization = _organization(client)
    workspace = _workspace(client, organization["organization_id"])
    issued = client.post(
        (f"/api/v4.4/directory/organizations/{organization['organization_id']}/api-keys"),
        json={
            "subject": {"type": "user", "id": "developer"},
            "name": "Workspace reader",
            "scopes": [
                "organization.read",
                "workspace.read",
                "decision.read",
            ],
            "expires_in_seconds": 3600,
        },
    ).get_json()
    client.delete("/api/v4.4/session/tenant")
    headers = {"X-API-Key": issued["credential"]}
    base = (
        f"/api/v4.4/directory/organizations/{organization['organization_id']}/"
        f"workspaces/{workspace['workspace_id']}"
    )
    assert client.get(f"{base}/decisions", headers=headers).status_code == 200
    denied = client.post(
        f"{base}/decisions",
        headers=headers,
        json={
            "operation": "decision.ensemble",
            "title": "No write scope",
            "payload": {"decision": "home"},
        },
    )
    assert denied.status_code == 403
