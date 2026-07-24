"""Dependency-light enterprise access contracts for NFL Analytics Hub v4.4.0."""

from __future__ import annotations

import hashlib
import json
import math
import re
import threading
from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import Any

VERSION = "4.4.0"
RELEASE_VERSION = "4.4.3"
MAX_TAGS = 20
MAX_MEMBERSHIPS_PER_DECISION = 100
MAX_ORGANIZATIONS = 1_000
MAX_MEMBERSHIPS = 10_000
MAX_METADATA_BYTES = 64 * 1024

_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}[a-z0-9]$")
_IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9._-]{0,79}$")
_SUBJECT_ID = re.compile(r"^[a-z0-9][a-z0-9._:@+-]{0,159}$")
_ORGANIZATION_ID = re.compile(r"^org_[a-f0-9]{20}$")
_MEMBERSHIP_ID = re.compile(r"^membership_[a-f0-9]{20}$")
_ORGANIZATION_STATUSES = {"active", "suspended", "archived"}
_MEMBERSHIP_STATUSES = {"invited", "active", "suspended", "removed"}
_SUBJECT_TYPES = {"user", "service"}

_PERMISSIONS = {
    "api-key.manage",
    "audit.read",
    "decision.execute",
    "decision.read",
    "membership.manage",
    "membership.read",
    "model.manage",
    "model.read",
    "organization.manage",
    "organization.read",
    "quota.manage",
    "quota.read",
    "workspace.manage",
    "workspace.read",
}

_ROLE_PERMISSIONS = {
    "owner": _PERMISSIONS,
    "admin": {
        "api-key.manage",
        "audit.read",
        "decision.execute",
        "decision.read",
        "membership.manage",
        "membership.read",
        "model.manage",
        "model.read",
        "organization.read",
        "quota.read",
        "workspace.manage",
        "workspace.read",
    },
    "analyst": {
        "decision.execute",
        "decision.read",
        "membership.read",
        "model.read",
        "organization.read",
        "workspace.manage",
        "workspace.read",
    },
    "viewer": {
        "decision.read",
        "model.read",
        "organization.read",
        "workspace.read",
    },
}


def _canonical_json(value: Any, field: str, maximum_bytes: int = MAX_METADATA_BYTES) -> str:
    try:
        raw = json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be JSON-safe and contain only finite numbers") from exc
    if len(raw.encode("utf-8")) > maximum_bytes:
        raise ValueError(f"{field} exceeds {maximum_bytes} bytes")
    return raw


def _digest(value: Any, field: str) -> str:
    raw = _canonical_json(value, field)
    return f"sha256:{hashlib.sha256(raw.encode()).hexdigest()}"


def _timestamp(value: Any, field: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a finite non-negative number") from exc
    if not math.isfinite(result) or result < 0:
        raise ValueError(f"{field} must be a finite non-negative number")
    return round(result, 6)


def _identifier(value: Any, field: str) -> str:
    result = str(value or "").strip().lower()
    if not _IDENTIFIER.fullmatch(result):
        raise ValueError(f"{field} must use 1-80 lowercase letters, numbers, dots, dashes, or underscores")
    return result


def _text(value: Any, field: str, maximum: int, *, required: bool = True) -> str | None:
    result = str(value or "").strip()
    if not result:
        if required:
            raise ValueError(f"{field} is required")
        return None
    if len(result) > maximum:
        raise ValueError(f"{field} cannot exceed {maximum} characters")
    return result


def _slug(value: Any) -> str:
    result = str(value or "").strip().lower()
    if not _SLUG.fullmatch(result):
        raise ValueError(
            "slug must use 3-64 lowercase letters, numbers, or single dashes "
            "and cannot begin or end with a dash"
        )
    return result


def _organization_id(value: Any) -> str:
    result = str(value or "").strip().lower()
    if not _ORGANIZATION_ID.fullmatch(result):
        raise ValueError("organization_id must be a normalized v4.4 organization identity")
    return result


def _subject(value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise ValueError("subject must be a JSON object")
    subject_type = str(value.get("type") or "").strip().lower()
    if subject_type not in _SUBJECT_TYPES:
        raise ValueError("subject type must be user or service")
    subject_id = str(value.get("id") or "").strip().lower()
    if not _SUBJECT_ID.fullmatch(subject_id):
        raise ValueError(
            "subject id must use 1-160 lowercase letters, numbers, dots, "
            "dashes, underscores, colons, at signs, or pluses"
        )
    return {"type": subject_type, "id": subject_id}


def _tags(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str | bytes) or not isinstance(value, Sequence):
        raise ValueError("tags must be a list")
    if len(value) > MAX_TAGS:
        raise ValueError(f"tags cannot contain more than {MAX_TAGS} items")
    normalized = [_identifier(item, "tag") for item in value]
    if len(set(normalized)) != len(normalized):
        raise ValueError("tags cannot contain duplicates")
    return sorted(normalized)


def normalize_organization(
    payload: Mapping[str, Any],
    *,
    created_at: Any = None,
) -> dict[str, Any]:
    """Normalize one deterministic organization contract."""
    if not isinstance(payload, Mapping):
        raise ValueError("organization must be a JSON object")
    slug = _slug(payload.get("slug"))
    status = str(payload.get("status", "active")).strip().lower()
    if status not in _ORGANIZATION_STATUSES:
        raise ValueError("organization status must be active, suspended, or archived")
    creator = _subject(payload.get("created_by"))
    data_region = payload.get("data_region")
    body = {
        "slug": slug,
        "name": _text(payload.get("name"), "organization name", 160),
        "status": status,
        "data_region": (None if data_region is None else _identifier(data_region, "data_region")),
        "tags": _tags(payload.get("tags")),
        "created_by": creator,
    }
    identity = hashlib.sha256(slug.encode()).hexdigest()[:20]
    timestamp = _timestamp(
        payload.get("created_at", 0) if created_at is None else created_at,
        "created_at",
    )
    return {
        "contract_version": VERSION,
        "organization_id": f"org_{identity}",
        **body,
        "metadata_digest": _digest(body, "organization metadata"),
        "created_at": timestamp,
    }


def normalize_membership(
    payload: Mapping[str, Any],
    *,
    granted_at: Any = None,
) -> dict[str, Any]:
    """Normalize one role-bearing organization membership."""
    if not isinstance(payload, Mapping):
        raise ValueError("membership must be a JSON object")
    organization_id = _organization_id(payload.get("organization_id"))
    subject = _subject(payload.get("subject"))
    role = str(payload.get("role") or "").strip().lower()
    if role not in _ROLE_PERMISSIONS:
        raise ValueError(f"role must be one of {', '.join(sorted(_ROLE_PERMISSIONS))}")
    if subject["type"] == "service" and role in {"owner", "admin"}:
        raise ValueError("service subjects cannot receive owner or admin roles")
    status = str(payload.get("status", "active")).strip().lower()
    if status not in _MEMBERSHIP_STATUSES:
        raise ValueError("membership status must be invited, active, suspended, or removed")
    granted_by = _subject(payload.get("granted_by"))
    body = {
        "organization_id": organization_id,
        "subject": subject,
        "role": role,
        "status": status,
        "granted_by": granted_by,
    }
    identity_body = {
        "organization_id": organization_id,
        "subject": subject,
    }
    identity = hashlib.sha256(_canonical_json(identity_body, "membership identity").encode()).hexdigest()[:20]
    timestamp = _timestamp(
        payload.get("granted_at", 0) if granted_at is None else granted_at,
        "granted_at",
    )
    return {
        "contract_version": VERSION,
        "membership_id": f"membership_{identity}",
        **body,
        "permissions": sorted(_ROLE_PERMISSIONS[role]),
        "metadata_digest": _digest(body, "membership metadata"),
        "granted_at": timestamp,
    }


def _validated_organization(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("organization must be a JSON object")
    if value.get("contract_version") != VERSION:
        raise ValueError(f"organization must use contract_version {VERSION}")
    normalized = normalize_organization(value, created_at=value.get("created_at"))
    if value.get("organization_id") != normalized["organization_id"]:
        raise ValueError("organization identity does not match its slug")
    if value.get("metadata_digest") != normalized["metadata_digest"]:
        raise ValueError("organization metadata_digest does not match its content")
    return normalized


def _validated_membership(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("each membership must be a JSON object")
    if value.get("contract_version") != VERSION:
        raise ValueError(f"membership must use contract_version {VERSION}")
    normalized = normalize_membership(value, granted_at=value.get("granted_at"))
    if value.get("membership_id") != normalized["membership_id"]:
        raise ValueError("membership identity does not match its organization and subject")
    if value.get("metadata_digest") != normalized["metadata_digest"]:
        raise ValueError("membership metadata_digest does not match its content")
    if value.get("permissions") != normalized["permissions"]:
        raise ValueError("membership permissions do not match its role")
    return normalized


def authorize_access(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Evaluate one explicit permission request using deny-by-default semantics."""
    if not isinstance(payload, Mapping):
        raise ValueError("access request must be a JSON object")
    organization = _validated_organization(payload.get("organization"))
    subject = _subject(payload.get("subject"))
    permission = str(payload.get("permission") or "").strip().lower()
    if permission not in _PERMISSIONS:
        raise ValueError("permission is not in the v4.4 permission catalog")
    memberships = payload.get("memberships", [])
    if isinstance(memberships, str | bytes) or not isinstance(memberships, Sequence):
        raise ValueError("memberships must be a list")
    if len(memberships) > MAX_MEMBERSHIPS_PER_DECISION:
        raise ValueError(f"memberships cannot contain more than {MAX_MEMBERSHIPS_PER_DECISION} items")

    matching: dict[str, dict[str, Any]] = {}
    for item in memberships:
        candidate_membership = _validated_membership(item)
        if (
            candidate_membership["organization_id"] == organization["organization_id"]
            and candidate_membership["subject"] == subject
        ):
            existing = matching.get(candidate_membership["membership_id"])
            if (
                existing is not None
                and existing["metadata_digest"] != candidate_membership["metadata_digest"]
            ):
                raise ValueError("access request contains conflicting memberships")
            matching[candidate_membership["membership_id"]] = candidate_membership

    membership: dict[str, Any] | None = None
    if matching:
        membership = next(iter(matching.values()))
    role = membership["role"] if membership is not None else None
    effective_permissions = membership["permissions"] if membership is not None else []
    if organization["status"] != "active":
        allowed, reason = False, "organization-inactive"
    elif membership is None:
        allowed, reason = False, "membership-not-found"
    elif membership["status"] != "active":
        allowed, reason = False, "membership-inactive"
    elif permission not in effective_permissions:
        allowed, reason = False, "permission-not-granted"
    else:
        allowed, reason = True, "permission-granted"

    decision_body = {
        "organization_id": organization["organization_id"],
        "subject": subject,
        "permission": permission,
        "organization_digest": organization["metadata_digest"],
        "membership_digest": (membership["metadata_digest"] if membership is not None else None),
        "allowed": allowed,
        "reason": reason,
    }
    identity = hashlib.sha256(_canonical_json(decision_body, "access decision").encode()).hexdigest()[:24]
    return {
        "version": VERSION,
        "decision_id": f"access_{identity}",
        "allowed": allowed,
        "reason": reason,
        "organization_id": organization["organization_id"],
        "subject": subject,
        "permission": permission,
        "role": role,
        "effective_permissions": effective_permissions if allowed else [],
        "evidence_digest": _digest(decision_body, "access decision evidence"),
    }


class InMemoryEnterpriseDirectory:
    """Bounded reference directory for deterministic tests and local development."""

    def __init__(
        self,
        *,
        max_organizations: int = MAX_ORGANIZATIONS,
        max_memberships: int = MAX_MEMBERSHIPS,
    ) -> None:
        self.max_organizations = max(1, int(max_organizations))
        self.max_memberships = max(1, int(max_memberships))
        self._organizations: dict[str, dict[str, Any]] = {}
        self._organization_order: list[str] = []
        self._memberships: dict[str, dict[str, Any]] = {}
        self._membership_order: list[str] = []
        self._lock = threading.RLock()

    @staticmethod
    def _register(
        records: dict[str, dict[str, Any]],
        order: list[str],
        record: dict[str, Any],
        *,
        identity_field: str,
        limit: int,
        conflict_message: str,
    ) -> dict[str, Any]:
        identity = record[identity_field]
        existing = records.get(identity)
        if existing is not None:
            if existing["metadata_digest"] != record["metadata_digest"]:
                raise ValueError(conflict_message)
            return {"accepted": False, "deduplicated": True, "record": deepcopy(existing)}
        while len(order) >= limit:
            removed = order.pop(0)
            records.pop(removed, None)
        records[identity] = record
        order.append(identity)
        return {"accepted": True, "deduplicated": False, "record": deepcopy(record)}

    def register_organization(
        self,
        payload: Mapping[str, Any],
        *,
        created_at: Any = None,
    ) -> dict[str, Any]:
        organization = normalize_organization(payload, created_at=created_at)
        with self._lock:
            return self._register(
                self._organizations,
                self._organization_order,
                organization,
                identity_field="organization_id",
                limit=self.max_organizations,
                conflict_message="organization slug conflicts with existing metadata",
            )

    def register_membership(
        self,
        payload: Mapping[str, Any],
        *,
        granted_at: Any = None,
    ) -> dict[str, Any]:
        membership = normalize_membership(payload, granted_at=granted_at)
        with self._lock:
            if membership["organization_id"] not in self._organizations:
                raise KeyError("organization not found")
            return self._register(
                self._memberships,
                self._membership_order,
                membership,
                identity_field="membership_id",
                limit=self.max_memberships,
                conflict_message="organization membership conflicts with existing metadata",
            )

    def authorize(
        self,
        organization_id: Any,
        subject: Mapping[str, Any],
        permission: Any,
    ) -> dict[str, Any]:
        identity = _organization_id(organization_id)
        normalized_subject = _subject(subject)
        with self._lock:
            organization = self._organizations.get(identity)
            if organization is None:
                raise KeyError("organization not found")
            memberships = [
                deepcopy(record)
                for record in self._memberships.values()
                if record["organization_id"] == identity and record["subject"] == normalized_subject
            ]
            return authorize_access(
                {
                    "organization": deepcopy(organization),
                    "memberships": memberships,
                    "subject": normalized_subject,
                    "permission": permission,
                }
            )


def role_catalog() -> list[dict[str, Any]]:
    """Return the immutable built-in role and permission mapping."""
    return [
        {
            "role": role,
            "permissions": sorted(permissions),
            "user_only": role in {"owner", "admin"},
        }
        for role, permissions in _ROLE_PERMISSIONS.items()
    ]


def enterprise_manifest() -> dict[str, Any]:
    """Describe the current v4.4 enterprise-access capabilities and boundaries."""
    return {
        "version": RELEASE_VERSION,
        "contract_version": VERSION,
        "name": "Enterprise Access",
        "features": {
            "deterministic_organization_identities": True,
            "deterministic_membership_identities": True,
            "fixed_role_permission_catalog": True,
            "deny_by_default_authorization": True,
            "organization_status_enforcement": True,
            "membership_status_enforcement": True,
            "contract_integrity_validation": True,
            "persistent_tenant_directory": True,
            "tenant_aware_sessions": True,
            "service_accounts": True,
            "enterprise_route_tenant_enforcement": True,
            "runtime_tenant_enforcement": False,
            "api_keys": True,
            "api_key_plaintext_storage": False,
            "api_key_scopes": True,
            "api_key_rotation": True,
            "api_key_expiry": True,
            "api_key_revocation": True,
            "redis_usage_accounting": True,
            "organization_quotas": True,
            "credential_quotas": True,
            "idempotent_request_metering": True,
            "inspectable_limit_responses": True,
            "quotas": True,
            "shared_workspaces": True,
            "workspace_collaboration": True,
            "saved_decisions": True,
            "enterprise_reports": True,
            "append_only_enterprise_audit": True,
            "enterprise_exports": True,
            "retention_controls": True,
            "retention_hard_delete": False,
            "public_decision_apis": True,
            "public_decision_api_key_required": True,
        },
        "roles": role_catalog(),
        "permissions": sorted(_PERMISSIONS),
        "organization_statuses": sorted(_ORGANIZATION_STATUSES),
        "membership_statuses": sorted(_MEMBERSHIP_STATUSES),
        "subject_types": sorted(_SUBJECT_TYPES),
        "limits": {
            "tags": MAX_TAGS,
            "memberships_per_decision": MAX_MEMBERSHIPS_PER_DECISION,
            "metadata_bytes": MAX_METADATA_BYTES,
            "reference_organizations": MAX_ORGANIZATIONS,
            "reference_memberships": MAX_MEMBERSHIPS,
        },
        "next_increment": None,
    }
