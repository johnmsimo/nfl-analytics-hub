import math

import pytest

from enterprise_v44 import (
    InMemoryEnterpriseDirectory,
    authorize_access,
    enterprise_manifest,
    normalize_membership,
    normalize_organization,
    role_catalog,
)


def _subject(identifier="john@example.com", subject_type="user"):
    return {"type": subject_type, "id": identifier}


def _organization(**overrides):
    payload = {
        "slug": "nfl-labs",
        "name": "NFL Labs",
        "created_by": _subject(),
        "tags": ["analytics", "football"],
    }
    payload.update(overrides)
    created_at = payload.pop("created_at", 100.0)
    return normalize_organization(payload, created_at=created_at)


def _membership(organization=None, **overrides):
    organization = organization or _organization()
    payload = {
        "organization_id": organization["organization_id"],
        "subject": _subject(),
        "role": "analyst",
        "status": "active",
        "granted_by": _subject("owner@example.com"),
    }
    payload.update(overrides)
    return normalize_membership(payload, granted_at=101.0)


def _access(permission="decision.execute", *, organization=None, membership=None):
    organization = organization or _organization()
    membership = membership or _membership(organization)
    return authorize_access(
        {
            "organization": organization,
            "memberships": [membership],
            "subject": _subject(),
            "permission": permission,
        }
    )


def test_organization_normalization_is_deterministic_and_order_independent():
    first = _organization()
    second = _organization(tags=["football", "analytics"])
    assert first == second
    assert first["organization_id"].startswith("org_")
    assert first["metadata_digest"].startswith("sha256:")
    assert first["tags"] == ["analytics", "football"]


@pytest.mark.parametrize(
    ("update", "message"),
    [
        ({"slug": "Bad Slug"}, "slug"),
        ({"status": "deleted"}, "status"),
        ({"created_at": math.nan}, "finite"),
        ({"tags": ["same", "same"]}, "duplicates"),
    ],
)
def test_organization_rejects_invalid_contracts(update, message):
    with pytest.raises(ValueError, match=message):
        _organization(**update)


def test_membership_identity_is_stable_across_role_metadata():
    organization = _organization()
    analyst = _membership(organization, role="analyst")
    viewer = _membership(organization, role="viewer")
    assert analyst["membership_id"] == viewer["membership_id"]
    assert analyst["metadata_digest"] != viewer["metadata_digest"]
    assert "decision.execute" in analyst["permissions"]
    assert "decision.execute" not in viewer["permissions"]


def test_service_subjects_cannot_receive_privileged_human_roles():
    organization = _organization()
    with pytest.raises(ValueError, match="service subjects"):
        _membership(
            organization,
            subject=_subject("model-worker", "service"),
            role="admin",
        )


def test_role_catalog_is_fixed_and_least_privilege():
    roles = {item["role"]: item for item in role_catalog()}
    assert set(roles) == {"owner", "admin", "analyst", "viewer"}
    assert "organization.manage" in roles["owner"]["permissions"]
    assert "organization.manage" not in roles["admin"]["permissions"]
    assert roles["viewer"]["permissions"] == [
        "decision.read",
        "model.read",
        "organization.read",
        "workspace.read",
    ]


def test_active_analyst_is_authorized_for_decision_execution():
    result = _access()
    assert result["allowed"] is True
    assert result["reason"] == "permission-granted"
    assert result["role"] == "analyst"
    assert result["decision_id"].startswith("access_")


def test_authorization_denies_permission_outside_role():
    result = _access("membership.manage")
    assert result["allowed"] is False
    assert result["reason"] == "permission-not-granted"
    assert result["effective_permissions"] == []


def test_authorization_denies_inactive_organization_before_role():
    organization = _organization(status="suspended")
    result = _access(organization=organization)
    assert result["allowed"] is False
    assert result["reason"] == "organization-inactive"


@pytest.mark.parametrize("status", ["invited", "suspended", "removed"])
def test_authorization_denies_non_active_memberships(status):
    organization = _organization()
    membership = _membership(organization, status=status)
    result = _access(organization=organization, membership=membership)
    assert result["allowed"] is False
    assert result["reason"] == "membership-inactive"


def test_authorization_denies_when_matching_membership_is_absent():
    organization = _organization()
    other = _membership(
        organization,
        subject=_subject("someone-else@example.com"),
    )
    result = authorize_access(
        {
            "organization": organization,
            "memberships": [other],
            "subject": _subject(),
            "permission": "decision.read",
        }
    )
    assert result["allowed"] is False
    assert result["reason"] == "membership-not-found"


def test_authorization_rejects_unknown_permissions_and_contract_versions():
    organization = _organization()
    membership = _membership(organization)
    with pytest.raises(ValueError, match="permission catalog"):
        _access("root.everything")
    organization["contract_version"] = "4.3.3"
    with pytest.raises(ValueError, match="contract_version 4.4.0"):
        _access(organization=organization, membership=membership)


def test_authorization_detects_metadata_and_permission_inconsistency():
    organization = _organization()
    membership = _membership(organization)
    organization["name"] = "Changed"
    with pytest.raises(ValueError, match="metadata_digest"):
        _access(organization=organization, membership=membership)

    organization = _organization()
    membership = _membership(organization)
    membership["permissions"].append("membership.manage")
    with pytest.raises(ValueError, match="permissions"):
        _access(organization=organization, membership=membership)


def test_reference_directory_deduplicates_and_detects_conflicts():
    directory = InMemoryEnterpriseDirectory()
    payload = {
        "slug": "nfl-labs",
        "name": "NFL Labs",
        "created_by": _subject(),
    }
    first = directory.register_organization(payload, created_at=100.0)
    second = directory.register_organization(payload, created_at=200.0)
    assert first["accepted"] is True
    assert second["deduplicated"] is True
    with pytest.raises(ValueError, match="conflicts"):
        directory.register_organization(
            {**payload, "name": "Different"},
            created_at=300.0,
        )


def test_reference_directory_authorizes_registered_membership():
    directory = InMemoryEnterpriseDirectory()
    organization = directory.register_organization(
        {
            "slug": "nfl-labs",
            "name": "NFL Labs",
            "created_by": _subject(),
        },
        created_at=100.0,
    )["record"]
    directory.register_membership(
        {
            "organization_id": organization["organization_id"],
            "subject": _subject(),
            "role": "owner",
            "granted_by": _subject(),
        },
        granted_at=101.0,
    )
    result = directory.authorize(
        organization["organization_id"],
        _subject(),
        "quota.manage",
    )
    assert result["allowed"] is True
    assert result["role"] == "owner"


def test_manifest_discloses_persistence_and_runtime_boundaries():
    manifest = enterprise_manifest()
    assert manifest["version"] == "4.4.1"
    assert manifest["contract_version"] == "4.4.0"
    assert manifest["features"]["deny_by_default_authorization"] is True
    assert manifest["features"]["persistent_tenant_directory"] is True
    assert manifest["features"]["enterprise_route_tenant_enforcement"] is True
    assert manifest["features"]["runtime_tenant_enforcement"] is False
    assert manifest["features"]["api_keys"] is True
    assert "v4.4.2" in manifest["next_increment"]
