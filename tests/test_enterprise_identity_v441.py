from datetime import UTC, datetime, timedelta

import pytest
from flask import Flask

from database import db
from db_models import EnterpriseApiKey, EnterpriseMembership
from enterprise_identity_v441 import (
    authenticate_api_key,
    authorize_context,
    authorize_persistent,
    bind_tenant,
    create_organization,
    issue_api_key,
    list_api_keys,
    register_membership,
    revoke_api_key,
    rotate_api_key,
)
from enterprise_v44 import normalize_membership

OWNER = {"type": "user", "id": "owner@example.com"}
SERVICE = {"type": "service", "id": "prediction-worker"}
NOW = datetime(2026, 7, 23, 20, 0, tzinfo=UTC)


@pytest.fixture()
def identity_app():
    app = Flask(__name__)
    app.config.update(
        TESTING=True,
        SECRET_KEY="test-enterprise-pepper",
        SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
    )
    db.init_app(app)
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


def _create_org():
    return create_organization(
        {"slug": "nfl-labs", "name": "NFL Labs"},
        actor=OWNER,
        now=NOW,
    )


def _create_service(organization_id, role="analyst"):
    return register_membership(
        {
            "organization_id": organization_id,
            "subject": SERVICE,
            "role": role,
        },
        actor=OWNER,
        now=NOW + timedelta(seconds=1),
    )


def test_persistent_organization_bootstraps_owner_atomically(identity_app):
    result = _create_org()
    assert result["accepted"] is True
    assert result["organization"]["organization_id"].startswith("org_")
    assert result["owner_membership"]["role"] == "owner"
    assert result["owner_membership"]["subject"] == OWNER
    decision = authorize_persistent(
        result["organization"]["organization_id"],
        OWNER,
        "organization.manage",
    )
    assert decision["allowed"] is True


def test_persistent_organization_deduplicates_and_detects_conflict(identity_app):
    first = _create_org()
    duplicate = _create_org()
    assert duplicate["accepted"] is False
    assert duplicate["deduplicated"] is True
    assert duplicate["organization"]["metadata_digest"] == first["organization"]["metadata_digest"]
    with pytest.raises(ValueError, match="conflicts"):
        create_organization(
            {"slug": "nfl-labs", "name": "Different"},
            actor=OWNER,
            now=NOW,
        )


def test_service_membership_is_persistent_and_authorizable(identity_app):
    organization_id = _create_org()["organization"]["organization_id"]
    membership = _create_service(organization_id)["membership"]
    assert membership["subject"] == SERVICE
    assert membership["role"] == "analyst"
    assert authorize_persistent(
        organization_id,
        SERVICE,
        "decision.execute",
    )["allowed"]
    assert not authorize_persistent(
        organization_id,
        SERVICE,
        "api-key.manage",
    )["allowed"]


def test_persistent_membership_deduplicates_but_rejects_metadata_change(
    identity_app,
):
    organization_id = _create_org()["organization"]["organization_id"]
    first = _create_service(organization_id)
    duplicate = _create_service(organization_id)
    assert first["accepted"] is True
    assert duplicate["deduplicated"] is True
    with pytest.raises(ValueError, match="conflicts"):
        _create_service(organization_id, role="viewer")


def test_tenant_binding_requires_active_membership(identity_app):
    organization_id = _create_org()["organization"]["organization_id"]
    context = bind_tenant(organization_id, OWNER)
    assert context["organization_id"] == organization_id
    assert context["authentication"] == "session"
    assert "api-key.manage" in context["permissions"]
    with pytest.raises(PermissionError, match="membership-not-found"):
        bind_tenant(
            organization_id,
            {"type": "user", "id": "absent@example.com"},
        )


def test_api_key_plaintext_is_returned_once_and_never_persisted(identity_app):
    organization_id = _create_org()["organization"]["organization_id"]
    _create_service(organization_id)
    result = issue_api_key(
        organization_id,
        subject=SERVICE,
        name="Prediction worker",
        scopes=["model.read", "decision.execute"],
        expires_in_seconds=3600,
        actor=OWNER,
        now=NOW,
    )
    assert result["credential"].startswith("nfl_v441_")
    record = db.session.get(EnterpriseApiKey, result["api_key_id"])
    assert record.secret_digest
    assert result["credential"] not in record.secret_digest
    metadata = list_api_keys(organization_id)[0]
    assert "credential" not in metadata
    assert "secret_digest" not in metadata


def test_api_key_scopes_cannot_exceed_membership(identity_app):
    organization_id = _create_org()["organization"]["organization_id"]
    _create_service(organization_id, role="viewer")
    with pytest.raises(ValueError, match="exceed membership"):
        issue_api_key(
            organization_id,
            subject=SERVICE,
            name="Over-scoped",
            scopes=["decision.execute"],
            expires_in_seconds=3600,
            actor=OWNER,
            now=NOW,
        )


def test_api_key_issue_rejects_tampered_membership_metadata(identity_app):
    organization_id = _create_org()["organization"]["organization_id"]
    membership = _create_service(organization_id)["membership"]
    record = db.session.get(
        EnterpriseMembership,
        membership["membership_id"],
    )
    record.permissions = ["organization.read", "model.read", "api-key.manage"]
    db.session.commit()
    with pytest.raises(ValueError, match="permissions do not match"):
        issue_api_key(
            organization_id,
            subject=SERVICE,
            name="Tampered",
            scopes=["model.read"],
            expires_in_seconds=3600,
            actor=OWNER,
            now=NOW,
        )


@pytest.mark.parametrize("ttl", [299, 365 * 24 * 60 * 60 + 1, "invalid"])
def test_api_key_ttl_is_bounded(identity_app, ttl):
    organization_id = _create_org()["organization"]["organization_id"]
    _create_service(organization_id)
    with pytest.raises(ValueError, match="expires_in_seconds"):
        issue_api_key(
            organization_id,
            subject=SERVICE,
            name="Bad TTL",
            scopes=["model.read"],
            expires_in_seconds=ttl,
            actor=OWNER,
            now=NOW,
        )


def test_api_key_authentication_returns_bounded_context(identity_app):
    organization_id = _create_org()["organization"]["organization_id"]
    membership = _create_service(organization_id)["membership"]
    issued = issue_api_key(
        organization_id,
        subject=SERVICE,
        name="Worker",
        scopes=["decision.execute"],
        expires_in_seconds=3600,
        actor=OWNER,
        now=NOW,
    )
    context = authenticate_api_key(
        issued["credential"],
        now=NOW + timedelta(seconds=2),
    )
    assert context["authentication"] == "api_key"
    assert context["membership_id"] == membership["membership_id"]
    assert context["permissions"] == ["decision.execute"]
    assert "credential" not in context


def test_api_key_rejects_wrong_secret_expiry_and_revocation(identity_app):
    organization_id = _create_org()["organization"]["organization_id"]
    _create_service(organization_id)
    issued = issue_api_key(
        organization_id,
        subject=SERVICE,
        name="Worker",
        scopes=["model.read"],
        expires_in_seconds=300,
        actor=OWNER,
        now=NOW,
    )
    with pytest.raises(PermissionError, match="invalid"):
        authenticate_api_key(issued["credential"] + "x", now=NOW)
    with pytest.raises(PermissionError, match="expired"):
        authenticate_api_key(
            issued["credential"],
            now=NOW + timedelta(seconds=300),
        )
    revoke_api_key(organization_id, issued["api_key_id"], now=NOW)
    with pytest.raises(PermissionError, match="revoked"):
        authenticate_api_key(issued["credential"], now=NOW)


def test_api_key_fails_closed_when_membership_is_suspended(identity_app):
    organization_id = _create_org()["organization"]["organization_id"]
    membership = _create_service(organization_id)["membership"]
    issued = issue_api_key(
        organization_id,
        subject=SERVICE,
        name="Worker",
        scopes=["model.read"],
        expires_in_seconds=3600,
        actor=OWNER,
        now=NOW,
    )
    record = db.session.get(
        EnterpriseMembership,
        membership["membership_id"],
    )
    updated = normalize_membership(
        {
            "organization_id": record.organization_id,
            "subject": {
                "type": record.subject_type,
                "id": record.subject_id,
            },
            "role": record.role,
            "status": "suspended",
            "granted_by": {
                "type": record.granted_by_type,
                "id": record.granted_by_id,
            },
        },
        granted_at=record.contract_granted_at,
    )
    record.status = updated["status"]
    record.metadata_digest = updated["metadata_digest"]
    db.session.commit()
    with pytest.raises(PermissionError, match="membership denied"):
        authenticate_api_key(issued["credential"], now=NOW)


def test_api_key_fails_closed_when_role_no_longer_covers_scope(identity_app):
    organization_id = _create_org()["organization"]["organization_id"]
    membership = _create_service(organization_id)["membership"]
    issued = issue_api_key(
        organization_id,
        subject=SERVICE,
        name="Worker",
        scopes=["decision.execute"],
        expires_in_seconds=3600,
        actor=OWNER,
        now=NOW,
    )
    record = db.session.get(
        EnterpriseMembership,
        membership["membership_id"],
    )
    updated = normalize_membership(
        {
            "organization_id": record.organization_id,
            "subject": {
                "type": record.subject_type,
                "id": record.subject_id,
            },
            "role": "viewer",
            "status": record.status,
            "granted_by": {
                "type": record.granted_by_type,
                "id": record.granted_by_id,
            },
        },
        granted_at=record.contract_granted_at,
    )
    record.role = updated["role"]
    record.permissions = updated["permissions"]
    record.metadata_digest = updated["metadata_digest"]
    db.session.commit()
    with pytest.raises(PermissionError, match="scopes exceed"):
        authenticate_api_key(issued["credential"], now=NOW)


def test_api_key_revocation_is_idempotent(identity_app):
    organization_id = _create_org()["organization"]["organization_id"]
    _create_service(organization_id)
    issued = issue_api_key(
        organization_id,
        subject=SERVICE,
        name="Worker",
        scopes=["model.read"],
        expires_in_seconds=3600,
        actor=OWNER,
        now=NOW,
    )
    first = revoke_api_key(organization_id, issued["api_key_id"], now=NOW)
    second = revoke_api_key(organization_id, issued["api_key_id"], now=NOW)
    assert first["revoked"] is True
    assert second["revoked"] is False


def test_api_key_rotation_revokes_old_and_returns_one_new_secret(identity_app):
    organization_id = _create_org()["organization"]["organization_id"]
    _create_service(organization_id)
    issued = issue_api_key(
        organization_id,
        subject=SERVICE,
        name="Worker",
        scopes=["model.read"],
        expires_in_seconds=3600,
        actor=OWNER,
        now=NOW,
    )
    rotated = rotate_api_key(
        organization_id,
        issued["api_key_id"],
        actor=OWNER,
        expires_in_seconds=7200,
        now=NOW + timedelta(seconds=10),
    )
    replacement = rotated["replacement"]
    assert replacement["rotated_from_id"] == issued["api_key_id"]
    assert replacement["credential"] != issued["credential"]
    with pytest.raises(PermissionError, match="revoked"):
        authenticate_api_key(issued["credential"], now=NOW + timedelta(seconds=11))
    assert (
        authenticate_api_key(
            replacement["credential"],
            now=NOW + timedelta(seconds=11),
        )["api_key_id"]
        == replacement["api_key_id"]
    )


def test_authorize_context_requires_matching_tenant_and_scope(identity_app):
    organization_id = _create_org()["organization"]["organization_id"]
    context = bind_tenant(organization_id, OWNER)
    assert authorize_context(
        context,
        organization_id,
        "membership.manage",
    )["allowed"]
    with pytest.raises(PermissionError, match="does not match"):
        authorize_context(context, "org_00000000000000000000", "membership.manage")
    context["permissions"] = ["organization.read"]
    with pytest.raises(PermissionError, match="outside"):
        authorize_context(context, organization_id, "membership.manage")
