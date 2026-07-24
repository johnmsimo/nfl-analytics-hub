"""Persistent enterprise identity and API-key services for NFL Analytics Hub v4.4.1."""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from flask import current_app
from sqlalchemy.exc import IntegrityError

from database import db
from db_models import EnterpriseApiKey, EnterpriseMembership, EnterpriseOrganization
from enterprise_v44 import authorize_access, normalize_membership, normalize_organization

VERSION = "4.4.1"
MIN_KEY_TTL_SECONDS = 300
MAX_KEY_TTL_SECONDS = 365 * 24 * 60 * 60
MAX_KEY_SCOPES = 20
_CREDENTIAL = re.compile(r"^nfl_v441_([a-f0-9]{16})_([A-Za-z0-9_-]{32,128})$")


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _unix(value: datetime) -> float:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return round(value.timestamp(), 6)


def _pepper() -> bytes:
    configured = os.getenv("API_KEY_PEPPER")
    if configured:
        return configured.encode()
    secret = current_app.config.get("SECRET_KEY")
    if not secret:
        raise RuntimeError("SECRET_KEY or API_KEY_PEPPER is required for API keys")
    return str(secret).encode()


def _credential_digest(credential: str) -> str:
    return hmac.new(_pepper(), credential.encode(), hashlib.sha256).hexdigest()


def _subject(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        raise ValueError("subject must be a JSON object")
    subject_type = str(value.get("type") or "").strip().lower()
    subject_id = str(value.get("id") or "").strip().lower()
    if subject_type not in {"user", "service"} or not subject_id:
        raise ValueError("subject must contain a user or service type and an id")
    return {"type": subject_type, "id": subject_id}


def _organization_contract(record: EnterpriseOrganization) -> dict[str, Any]:
    return {
        "contract_version": record.contract_version,
        "organization_id": record.organization_id,
        "slug": record.slug,
        "name": record.name,
        "status": record.status,
        "data_region": record.data_region,
        "tags": list(record.tags or []),
        "created_by": {
            "type": record.created_by_type,
            "id": record.created_by_id,
        },
        "metadata_digest": record.metadata_digest,
        "created_at": record.contract_created_at,
    }


def _membership_contract(record: EnterpriseMembership) -> dict[str, Any]:
    return {
        "contract_version": record.contract_version,
        "membership_id": record.membership_id,
        "organization_id": record.organization_id,
        "subject": {
            "type": record.subject_type,
            "id": record.subject_id,
        },
        "role": record.role,
        "status": record.status,
        "granted_by": {
            "type": record.granted_by_type,
            "id": record.granted_by_id,
        },
        "permissions": list(record.permissions or []),
        "metadata_digest": record.metadata_digest,
        "granted_at": record.contract_granted_at,
    }


def _api_key_metadata(record: EnterpriseApiKey) -> dict[str, Any]:
    return {
        "version": VERSION,
        "api_key_id": record.api_key_id,
        "organization_id": record.organization_id,
        "membership_id": record.membership_id,
        "subject": {
            "type": record.subject_type,
            "id": record.subject_id,
        },
        "name": record.name,
        "prefix": record.prefix,
        "scopes": list(record.scopes or []),
        "status": record.status,
        "issued_at": _unix(record.issued_at),
        "expires_at": _unix(record.expires_at),
        "last_used_at": (_unix(record.last_used_at) if record.last_used_at is not None else None),
        "revoked_at": (_unix(record.revoked_at) if record.revoked_at is not None else None),
        "rotated_from_id": record.rotated_from_id,
    }


def create_organization(
    payload: dict[str, Any],
    *,
    actor: dict[str, str],
    now: datetime | None = None,
) -> dict[str, Any]:
    """Persist one organization and its initial owner membership atomically."""
    actor = _subject(actor)
    if actor["type"] != "user":
        raise ValueError("organizations must be created by a user subject")
    timestamp = now or _utcnow()
    organization = normalize_organization(
        {
            **payload,
            "created_by": actor,
        },
        created_at=_unix(timestamp),
    )
    existing = db.session.get(
        EnterpriseOrganization,
        organization["organization_id"],
    )
    if existing is not None:
        if existing.metadata_digest != organization["metadata_digest"]:
            raise ValueError("organization slug conflicts with existing metadata")
        owner = EnterpriseMembership.query.filter_by(
            organization_id=existing.organization_id,
            subject_type=actor["type"],
            subject_id=actor["id"],
        ).one_or_none()
        return {
            "accepted": False,
            "deduplicated": True,
            "organization": _organization_contract(existing),
            "owner_membership": (_membership_contract(owner) if owner is not None else None),
        }

    owner = normalize_membership(
        {
            "organization_id": organization["organization_id"],
            "subject": actor,
            "role": "owner",
            "status": "active",
            "granted_by": actor,
        },
        granted_at=_unix(timestamp),
    )
    organization_record = EnterpriseOrganization(
        organization_id=organization["organization_id"],
        slug=organization["slug"],
        name=organization["name"],
        status=organization["status"],
        data_region=organization["data_region"],
        tags=organization["tags"],
        created_by_type=actor["type"],
        created_by_id=actor["id"],
        metadata_digest=organization["metadata_digest"],
        contract_version=organization["contract_version"],
        contract_created_at=organization["created_at"],
    )
    try:
        db.session.add(organization_record)
        db.session.flush()
        db.session.add(
            EnterpriseMembership(
                membership_id=owner["membership_id"],
                organization_id=owner["organization_id"],
                subject_type=owner["subject"]["type"],
                subject_id=owner["subject"]["id"],
                role=owner["role"],
                status=owner["status"],
                permissions=owner["permissions"],
                granted_by_type=owner["granted_by"]["type"],
                granted_by_id=owner["granted_by"]["id"],
                metadata_digest=owner["metadata_digest"],
                contract_version=owner["contract_version"],
                contract_granted_at=owner["granted_at"],
            )
        )
        db.session.commit()
    except IntegrityError as exc:
        db.session.rollback()
        raise ValueError("organization conflicts with existing persistent identity") from exc
    return {
        "accepted": True,
        "deduplicated": False,
        "organization": organization,
        "owner_membership": owner,
    }


def get_organization(organization_id: str) -> dict[str, Any]:
    record = db.session.get(EnterpriseOrganization, organization_id)
    if record is None:
        raise KeyError("organization not found")
    return _organization_contract(record)


def register_membership(
    payload: dict[str, Any],
    *,
    actor: dict[str, str],
    now: datetime | None = None,
) -> dict[str, Any]:
    """Persist or idempotently return one organization membership."""
    actor = _subject(actor)
    organization_id = str(payload.get("organization_id") or "").strip().lower()
    if db.session.get(EnterpriseOrganization, organization_id) is None:
        raise KeyError("organization not found")
    membership = normalize_membership(
        {
            **payload,
            "granted_by": actor,
        },
        granted_at=_unix(now or _utcnow()),
    )
    existing = db.session.get(EnterpriseMembership, membership["membership_id"])
    if existing is not None:
        if existing.metadata_digest != membership["metadata_digest"]:
            raise ValueError("organization membership conflicts with existing metadata")
        return {
            "accepted": False,
            "deduplicated": True,
            "membership": _membership_contract(existing),
        }
    db.session.add(
        EnterpriseMembership(
            membership_id=membership["membership_id"],
            organization_id=membership["organization_id"],
            subject_type=membership["subject"]["type"],
            subject_id=membership["subject"]["id"],
            role=membership["role"],
            status=membership["status"],
            permissions=membership["permissions"],
            granted_by_type=membership["granted_by"]["type"],
            granted_by_id=membership["granted_by"]["id"],
            metadata_digest=membership["metadata_digest"],
            contract_version=membership["contract_version"],
            contract_granted_at=membership["granted_at"],
        )
    )
    try:
        db.session.commit()
    except IntegrityError as exc:
        db.session.rollback()
        raise ValueError("membership conflicts with existing persistent identity") from exc
    return {
        "accepted": True,
        "deduplicated": False,
        "membership": membership,
    }


def authorize_persistent(
    organization_id: str,
    subject: Any,
    permission: str,
) -> dict[str, Any]:
    organization = db.session.get(EnterpriseOrganization, organization_id)
    if organization is None:
        raise KeyError("organization not found")
    subject = _subject(subject)
    membership = EnterpriseMembership.query.filter_by(
        organization_id=organization_id,
        subject_type=subject["type"],
        subject_id=subject["id"],
    ).one_or_none()
    return authorize_access(
        {
            "organization": _organization_contract(organization),
            "memberships": ([_membership_contract(membership)] if membership is not None else []),
            "subject": subject,
            "permission": permission,
        }
    )


def bind_tenant(
    organization_id: str,
    subject: dict[str, str],
) -> dict[str, Any]:
    """Resolve one active persistent membership into a session-safe context."""
    decision = authorize_persistent(
        organization_id,
        subject,
        "organization.read",
    )
    if not decision["allowed"]:
        raise PermissionError(decision["reason"])
    membership = EnterpriseMembership.query.filter_by(
        organization_id=organization_id,
        subject_type=decision["subject"]["type"],
        subject_id=decision["subject"]["id"],
    ).one()
    return {
        "version": VERSION,
        "authentication": "session",
        "organization_id": organization_id,
        "membership_id": membership.membership_id,
        "subject": decision["subject"],
        "role": decision["role"],
        "permissions": list(membership.permissions or []),
    }


def _normalize_scopes(
    scopes: Any,
    membership: EnterpriseMembership,
) -> list[str]:
    if not isinstance(scopes, list) or not scopes:
        raise ValueError("scopes must be a non-empty list")
    if len(scopes) > MAX_KEY_SCOPES:
        raise ValueError(f"scopes cannot contain more than {MAX_KEY_SCOPES} items")
    normalized = sorted({str(item or "").strip().lower() for item in scopes})
    if len(normalized) != len(scopes) or any(not item for item in normalized):
        raise ValueError("scopes must contain unique non-empty permissions")
    permitted = set(membership.permissions or [])
    unknown = sorted(set(normalized) - permitted)
    if unknown:
        raise ValueError(f"scopes exceed membership permissions: {', '.join(unknown)}")
    return normalized


def issue_api_key(
    organization_id: str,
    *,
    subject: Any,
    name: Any,
    scopes: Any,
    expires_in_seconds: Any,
    actor: dict[str, str],
    now: datetime | None = None,
    rotated_from_id: str | None = None,
    commit: bool = True,
) -> dict[str, Any]:
    """Issue a high-entropy credential and persist only its peppered digest."""
    subject = _subject(subject)
    actor = _subject(actor)
    membership = EnterpriseMembership.query.filter_by(
        organization_id=organization_id,
        subject_type=subject["type"],
        subject_id=subject["id"],
    ).one_or_none()
    if membership is None or membership.status != "active":
        raise ValueError("API key subject must have an active membership")
    subject_decision = authorize_persistent(
        organization_id,
        subject,
        "organization.read",
    )
    if not subject_decision["allowed"]:
        raise ValueError("API key subject is not authorized in the active organization")
    normalized_scopes = _normalize_scopes(scopes, membership)
    key_name = str(name or "").strip()
    if not key_name or len(key_name) > 120:
        raise ValueError("API key name must contain 1-120 characters")
    try:
        ttl = int(expires_in_seconds)
    except (TypeError, ValueError) as exc:
        raise ValueError("expires_in_seconds must be an integer") from exc
    if not MIN_KEY_TTL_SECONDS <= ttl <= MAX_KEY_TTL_SECONDS:
        raise ValueError(
            f"expires_in_seconds must be between {MIN_KEY_TTL_SECONDS} and {MAX_KEY_TTL_SECONDS}"
        )

    issued_at = now or _utcnow()
    prefix = secrets.token_hex(8)
    secret = secrets.token_urlsafe(32)
    credential = f"nfl_v441_{prefix}_{secret}"
    api_key_id = f"apikey_{hashlib.sha256(prefix.encode()).hexdigest()[:20]}"
    record = EnterpriseApiKey(
        api_key_id=api_key_id,
        organization_id=organization_id,
        membership_id=membership.membership_id,
        subject_type=subject["type"],
        subject_id=subject["id"],
        name=key_name,
        prefix=prefix,
        secret_digest=_credential_digest(credential),
        scopes=normalized_scopes,
        status="active",
        issued_by_type=actor["type"],
        issued_by_id=actor["id"],
        issued_at=issued_at,
        expires_at=issued_at + timedelta(seconds=ttl),
        rotated_from_id=rotated_from_id,
    )
    db.session.add(record)
    if commit:
        db.session.commit()
    return {
        **_api_key_metadata(record),
        "credential": credential,
        "credential_visible_once": True,
    }


def list_api_keys(organization_id: str) -> list[dict[str, Any]]:
    records = (
        EnterpriseApiKey.query.filter_by(organization_id=organization_id)
        .order_by(EnterpriseApiKey.issued_at.desc())
        .all()
    )
    return [_api_key_metadata(record) for record in records]


def authenticate_api_key(
    credential: str,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Validate one credential and return bounded tenant context."""
    match = _CREDENTIAL.fullmatch(str(credential or ""))
    if match is None:
        raise PermissionError("invalid API credential")
    prefix = match.group(1)
    record = EnterpriseApiKey.query.filter_by(prefix=prefix).one_or_none()
    if record is None or not hmac.compare_digest(
        record.secret_digest,
        _credential_digest(credential),
    ):
        raise PermissionError("invalid API credential")
    current = now or _utcnow()
    expires_at = record.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if record.status != "active":
        raise PermissionError("API credential is revoked")
    if current >= expires_at:
        raise PermissionError("API credential is expired")

    decision = authorize_persistent(
        record.organization_id,
        {"type": record.subject_type, "id": record.subject_id},
        "organization.read",
    )
    if not decision["allowed"]:
        raise PermissionError(f"API credential membership denied: {decision['reason']}")
    membership = db.session.get(EnterpriseMembership, record.membership_id)
    if membership is None or not set(record.scopes or []).issubset(set(membership.permissions or [])):
        raise PermissionError("API credential scopes exceed current membership")
    record.last_used_at = current
    db.session.commit()
    return {
        "version": VERSION,
        "authentication": "api_key",
        "api_key_id": record.api_key_id,
        "organization_id": record.organization_id,
        "membership_id": record.membership_id,
        "subject": {
            "type": record.subject_type,
            "id": record.subject_id,
        },
        "role": membership.role,
        "permissions": list(record.scopes or []),
        "expires_at": _unix(record.expires_at),
    }


def revoke_api_key(
    organization_id: str,
    api_key_id: str,
    *,
    now: datetime | None = None,
    commit: bool = True,
) -> dict[str, Any]:
    record = db.session.get(EnterpriseApiKey, api_key_id)
    if record is None or record.organization_id != organization_id:
        raise KeyError("API key not found")
    changed = record.status != "revoked"
    if changed:
        record.status = "revoked"
        record.revoked_at = now or _utcnow()
    if commit:
        db.session.commit()
    return {"revoked": changed, "api_key": _api_key_metadata(record)}


def rotate_api_key(
    organization_id: str,
    api_key_id: str,
    *,
    actor: dict[str, str],
    expires_in_seconds: Any,
    now: datetime | None = None,
) -> dict[str, Any]:
    record = db.session.get(EnterpriseApiKey, api_key_id)
    if record is None or record.organization_id != organization_id or record.status != "active":
        raise KeyError("active API key not found")
    current = now or _utcnow()
    old_metadata = revoke_api_key(
        organization_id,
        api_key_id,
        now=current,
        commit=False,
    )
    replacement = issue_api_key(
        organization_id,
        subject={"type": record.subject_type, "id": record.subject_id},
        name=record.name,
        scopes=list(record.scopes or []),
        expires_in_seconds=expires_in_seconds,
        actor=actor,
        now=current,
        rotated_from_id=record.api_key_id,
        commit=False,
    )
    db.session.commit()
    return {"rotated": old_metadata["revoked"], "replacement": replacement}


def authorize_context(
    context: dict[str, Any],
    organization_id: str,
    permission: str,
) -> dict[str, Any]:
    """Revalidate a request context against current persistent state and scopes."""
    if context.get("organization_id") != organization_id:
        raise PermissionError("tenant context does not match organization")
    if permission not in set(context.get("permissions") or []):
        raise PermissionError("permission is outside the authenticated context")
    subject = context.get("subject")
    if not isinstance(subject, dict):
        raise PermissionError("authenticated context has no valid subject")
    decision = authorize_persistent(
        organization_id,
        subject,
        permission,
    )
    if not decision["allowed"]:
        raise PermissionError(decision["reason"])
    return decision
