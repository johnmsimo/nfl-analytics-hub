"""Tenant-scoped workspaces and enterprise operations for NFL Analytics Hub v4.4.3."""

from __future__ import annotations

import hashlib
import json
import re
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.exc import IntegrityError

from database import db
from db_models import (
    EnterpriseAuditEvent,
    EnterpriseMembership,
    EnterpriseOrganization,
    EnterpriseReport,
    EnterpriseRetentionPolicy,
    EnterpriseSavedDecision,
    EnterpriseWorkspace,
    EnterpriseWorkspaceCollaborator,
)

VERSION = "4.4.3"
DEFAULT_DECISION_RETENTION_DAYS = 365
DEFAULT_REPORT_RETENTION_DAYS = 365
MAX_RETENTION_DAYS = 3650
MAX_DECISION_BYTES = 128 * 1024
MAX_REPORT_BYTES = 256 * 1024
MAX_AUDIT_METADATA_BYTES = 32 * 1024
MAX_TAGS = 20
MAX_REPORT_DECISIONS = 100
MAX_EXPORT_RECORDS = 10_000

_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}[a-z0-9]$")
_ACCESS_LEVELS = {"viewer", "editor", "manager"}
_ACCESS_RANK = {"viewer": 1, "editor": 2, "manager": 3}
_WORKSPACE_STATUSES = {"active", "archived"}
_REPORT_STATUSES = {"draft", "published", "archived"}


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _unix(value: datetime | None) -> float | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return round(value.timestamp(), 6)


def _canonical_json(value: Any, field: str, maximum_bytes: int) -> str:
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


def _digest(value: Any, field: str, maximum_bytes: int) -> str:
    raw = _canonical_json(value, field, maximum_bytes)
    return f"sha256:{hashlib.sha256(raw.encode()).hexdigest()}"


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
            "workspace slug must use 3-64 lowercase letters, numbers, or single dashes "
            "and cannot begin or end with a dash"
        )
    return result


def _tags(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > MAX_TAGS:
        raise ValueError(f"tags must be a list with at most {MAX_TAGS} items")
    normalized = sorted({str(item or "").strip().lower() for item in value})
    if len(normalized) != len(value) or any(not item or len(item) > 80 for item in normalized):
        raise ValueError("tags must contain unique non-empty values of at most 80 characters")
    return normalized


def _actor_membership(
    organization_id: str,
    context: dict[str, Any],
) -> EnterpriseMembership:
    membership_id = str(context.get("membership_id") or "")
    record = db.session.get(EnterpriseMembership, membership_id)
    if (
        record is None
        or record.organization_id != organization_id
        or record.status != "active"
        or record.subject_type != context.get("subject", {}).get("type")
        or record.subject_id != context.get("subject", {}).get("id")
    ):
        raise PermissionError("authenticated membership is not active in this organization")
    return record


def _workspace_record(
    organization_id: str,
    workspace_id: str,
) -> EnterpriseWorkspace:
    record = db.session.get(EnterpriseWorkspace, workspace_id)
    if record is None or record.organization_id != organization_id:
        raise KeyError("workspace not found")
    return record


def _workspace_access_level(
    workspace: EnterpriseWorkspace,
    membership: EnterpriseMembership,
) -> str | None:
    if membership.role in {"owner", "admin"}:
        return "manager"
    if workspace.created_by_membership_id == membership.membership_id:
        return "manager"
    collaborator = EnterpriseWorkspaceCollaborator.query.filter_by(
        workspace_id=workspace.workspace_id,
        membership_id=membership.membership_id,
    ).one_or_none()
    return collaborator.access_level if collaborator is not None else None


def _require_workspace_access(
    workspace: EnterpriseWorkspace,
    membership: EnterpriseMembership,
    required: str,
) -> str:
    level = _workspace_access_level(workspace, membership)
    if level is None or _ACCESS_RANK[level] < _ACCESS_RANK[required]:
        raise PermissionError(f"workspace {required} access is required")
    if required != "viewer" and workspace.status != "active":
        raise PermissionError("archived workspaces are read-only")
    return level


def _policy_values(organization_id: str) -> tuple[int, int, bool]:
    policy = db.session.get(EnterpriseRetentionPolicy, organization_id)
    if policy is None:
        return (
            DEFAULT_DECISION_RETENTION_DAYS,
            DEFAULT_REPORT_RETENTION_DAYS,
            True,
        )
    return policy.decision_days, policy.report_days, policy.export_enabled


def _workspace_json(
    record: EnterpriseWorkspace,
    *,
    access_level: str | None = None,
) -> dict[str, Any]:
    result = {
        "version": VERSION,
        "workspace_id": record.workspace_id,
        "organization_id": record.organization_id,
        "slug": record.slug,
        "name": record.name,
        "description": record.description,
        "status": record.status,
        "created_by_membership_id": record.created_by_membership_id,
        "created_at": _unix(record.created_at),
        "updated_at": _unix(record.updated_at),
    }
    if access_level is not None:
        result["access_level"] = access_level
    return result


def _collaborator_json(record: EnterpriseWorkspaceCollaborator) -> dict[str, Any]:
    return {
        "version": VERSION,
        "collaborator_id": record.collaborator_id,
        "organization_id": record.organization_id,
        "workspace_id": record.workspace_id,
        "membership_id": record.membership_id,
        "access_level": record.access_level,
        "granted_by_membership_id": record.granted_by_membership_id,
        "created_at": _unix(record.created_at),
        "updated_at": _unix(record.updated_at),
    }


def _decision_json(record: EnterpriseSavedDecision) -> dict[str, Any]:
    if record.status == "active":
        if record.payload is None:
            raise ValueError("active saved decision is missing its payload")
        expected_digest = _digest(
            record.payload,
            "decision payload",
            MAX_DECISION_BYTES,
        )
        if record.payload_digest != expected_digest:
            raise ValueError("saved decision payload_digest does not match its content")
    elif record.status == "expired" and record.payload is not None:
        raise ValueError("expired saved decision content was not redacted")
    return {
        "version": VERSION,
        "decision_id": record.decision_id,
        "organization_id": record.organization_id,
        "workspace_id": record.workspace_id,
        "operation": record.operation,
        "title": record.title,
        "payload": record.payload,
        "payload_digest": record.payload_digest,
        "tags": list(record.tags or []),
        "status": record.status,
        "created_by_membership_id": record.created_by_membership_id,
        "retained_until": _unix(record.retained_until),
        "expired_at": _unix(record.expired_at),
        "created_at": _unix(record.created_at),
        "updated_at": _unix(record.updated_at),
    }


def _report_json(record: EnterpriseReport) -> dict[str, Any]:
    if record.status != "expired":
        if record.content is None:
            raise ValueError("active report is missing its content")
        expected_digest = _digest(
            record.content,
            "report content",
            MAX_REPORT_BYTES,
        )
        if record.content_digest != expected_digest:
            raise ValueError("report content_digest does not match its content")
    elif record.content is not None:
        raise ValueError("expired report content was not redacted")
    return {
        "version": VERSION,
        "report_id": record.report_id,
        "organization_id": record.organization_id,
        "workspace_id": record.workspace_id,
        "title": record.title,
        "content": record.content,
        "content_digest": record.content_digest,
        "decision_ids": list(record.decision_ids or []),
        "status": record.status,
        "created_by_membership_id": record.created_by_membership_id,
        "retained_until": _unix(record.retained_until),
        "expired_at": _unix(record.expired_at),
        "created_at": _unix(record.created_at),
        "updated_at": _unix(record.updated_at),
    }


def _audit_body(record: EnterpriseAuditEvent) -> dict[str, Any]:
    return {
        "event_id": record.event_id,
        "organization_id": record.organization_id,
        "workspace_id": record.workspace_id,
        "actor_membership_id": record.actor_membership_id,
        "action": record.action,
        "resource_type": record.resource_type,
        "resource_id": record.resource_id,
        "metadata": record.metadata_json,
        "previous_digest": record.previous_digest,
        "occurred_at": _unix(record.occurred_at),
    }


def _audit_json(record: EnterpriseAuditEvent) -> dict[str, Any]:
    return {
        "version": VERSION,
        "sequence": record.sequence,
        **_audit_body(record),
        "event_digest": record.event_digest,
    }


def _append_audit(
    organization_id: str,
    membership: EnterpriseMembership,
    action: str,
    resource_type: str,
    resource_id: str,
    metadata: dict[str, Any],
    *,
    workspace_id: str | None = None,
    now: datetime | None = None,
) -> EnterpriseAuditEvent:
    _canonical_json(metadata, "audit metadata", MAX_AUDIT_METADATA_BYTES)
    occurred_at = now or _utcnow()
    organization = db.session.get(
        EnterpriseOrganization,
        organization_id,
        with_for_update=True,
    )
    if organization is None:
        raise KeyError("organization not found")
    previous = (
        EnterpriseAuditEvent.query.filter_by(organization_id=organization_id)
        .order_by(EnterpriseAuditEvent.sequence.desc())
        .with_for_update()
        .first()
    )
    previous_digest = previous.event_digest if previous is not None else None
    event_id = f"audit_{secrets.token_hex(10)}"
    body = {
        "event_id": event_id,
        "organization_id": organization_id,
        "workspace_id": workspace_id,
        "actor_membership_id": membership.membership_id,
        "action": _text(action, "audit action", 100),
        "resource_type": _text(resource_type, "audit resource type", 40),
        "resource_id": _text(resource_id, "audit resource id", 80),
        "metadata": metadata,
        "previous_digest": previous_digest,
        "occurred_at": _unix(occurred_at),
    }
    record = EnterpriseAuditEvent(
        event_id=event_id,
        organization_id=organization_id,
        workspace_id=workspace_id,
        actor_membership_id=membership.membership_id,
        action=body["action"],
        resource_type=body["resource_type"],
        resource_id=body["resource_id"],
        metadata_json=metadata,
        previous_digest=previous_digest,
        event_digest=_digest(body, "audit event", MAX_AUDIT_METADATA_BYTES + 4096),
        occurred_at=occurred_at,
    )
    db.session.add(record)
    return record


def create_workspace(
    organization_id: str,
    payload: dict[str, Any],
    context: dict[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    membership = _actor_membership(organization_id, context)
    slug = _slug(payload.get("slug"))
    name = _text(payload.get("name"), "workspace name", 160)
    description = _text(
        payload.get("description"),
        "workspace description",
        1000,
        required=False,
    )
    workspace_id = "workspace_" + hashlib.sha256(f"{organization_id}:{slug}".encode()).hexdigest()[:20]
    existing = db.session.get(EnterpriseWorkspace, workspace_id)
    if existing is not None:
        if existing.name != name or existing.description != description:
            raise ValueError("workspace slug conflicts with existing metadata")
        return {
            "accepted": False,
            "deduplicated": True,
            "workspace": _workspace_json(
                existing,
                access_level=_workspace_access_level(existing, membership),
            ),
        }
    record = EnterpriseWorkspace(
        workspace_id=workspace_id,
        organization_id=organization_id,
        slug=slug,
        name=name,
        description=description,
        status="active",
        created_by_membership_id=membership.membership_id,
    )
    db.session.add(record)
    db.session.flush()
    _append_audit(
        organization_id,
        membership,
        "workspace.created",
        "workspace",
        workspace_id,
        {"slug": slug, "name": name},
        workspace_id=workspace_id,
        now=now,
    )
    try:
        db.session.commit()
    except IntegrityError as exc:
        db.session.rollback()
        raise ValueError("workspace conflicts with existing tenant state") from exc
    return {
        "accepted": True,
        "deduplicated": False,
        "workspace": _workspace_json(record, access_level="manager"),
    }


def list_workspaces(
    organization_id: str,
    context: dict[str, Any],
) -> list[dict[str, Any]]:
    membership = _actor_membership(organization_id, context)
    records = (
        EnterpriseWorkspace.query.filter_by(organization_id=organization_id)
        .order_by(EnterpriseWorkspace.created_at.desc())
        .all()
    )
    result = []
    for record in records:
        access = _workspace_access_level(record, membership)
        if access is not None:
            result.append(_workspace_json(record, access_level=access))
    return result


def update_workspace(
    organization_id: str,
    workspace_id: str,
    payload: dict[str, Any],
    context: dict[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    membership = _actor_membership(organization_id, context)
    record = _workspace_record(organization_id, workspace_id)
    _require_workspace_access(record, membership, "manager")
    changed: dict[str, Any] = {}
    if "name" in payload:
        changed["name"] = _text(payload.get("name"), "workspace name", 160)
    if "description" in payload:
        changed["description"] = _text(
            payload.get("description"),
            "workspace description",
            1000,
            required=False,
        )
    if "status" in payload:
        status = str(payload.get("status") or "").strip().lower()
        if status not in _WORKSPACE_STATUSES:
            raise ValueError("workspace status must be active or archived")
        changed["status"] = status
    if not changed:
        raise ValueError("workspace update must include name, description, or status")
    for field, value in changed.items():
        setattr(record, field, value)
    _append_audit(
        organization_id,
        membership,
        "workspace.updated",
        "workspace",
        workspace_id,
        {"changes": changed},
        workspace_id=workspace_id,
        now=now,
    )
    db.session.commit()
    return _workspace_json(
        record,
        access_level=_workspace_access_level(record, membership),
    )


def list_collaborators(
    organization_id: str,
    workspace_id: str,
    context: dict[str, Any],
) -> list[dict[str, Any]]:
    membership = _actor_membership(organization_id, context)
    workspace = _workspace_record(organization_id, workspace_id)
    _require_workspace_access(workspace, membership, "viewer")
    records = (
        EnterpriseWorkspaceCollaborator.query.filter_by(
            organization_id=organization_id,
            workspace_id=workspace_id,
        )
        .order_by(EnterpriseWorkspaceCollaborator.created_at.asc())
        .all()
    )
    return [_collaborator_json(record) for record in records]


def set_collaborator(
    organization_id: str,
    workspace_id: str,
    payload: dict[str, Any],
    context: dict[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    actor = _actor_membership(organization_id, context)
    workspace = _workspace_record(organization_id, workspace_id)
    _require_workspace_access(workspace, actor, "manager")
    membership_id = str(payload.get("membership_id") or "").strip()
    target = db.session.get(EnterpriseMembership, membership_id)
    if target is None or target.organization_id != organization_id or target.status != "active":
        raise ValueError("collaborator must be an active membership in this organization")
    level = str(payload.get("access_level") or "").strip().lower()
    if level not in _ACCESS_LEVELS:
        raise ValueError("access_level must be viewer, editor, or manager")
    identity = hashlib.sha256(f"{workspace_id}:{membership_id}".encode()).hexdigest()[:20]
    record = EnterpriseWorkspaceCollaborator.query.filter_by(
        workspace_id=workspace_id,
        membership_id=membership_id,
    ).one_or_none()
    created = record is None
    if record is None:
        record = EnterpriseWorkspaceCollaborator(
            collaborator_id=f"collaborator_{identity}",
            organization_id=organization_id,
            workspace_id=workspace_id,
            membership_id=membership_id,
            access_level=level,
            granted_by_membership_id=actor.membership_id,
        )
        db.session.add(record)
    else:
        record.access_level = level
        record.granted_by_membership_id = actor.membership_id
    _append_audit(
        organization_id,
        actor,
        "workspace.collaborator.set",
        "workspace_collaborator",
        record.collaborator_id,
        {"membership_id": membership_id, "access_level": level},
        workspace_id=workspace_id,
        now=now,
    )
    db.session.commit()
    return {"created": created, "collaborator": _collaborator_json(record)}


def remove_collaborator(
    organization_id: str,
    workspace_id: str,
    membership_id: str,
    context: dict[str, Any],
    *,
    now: datetime | None = None,
) -> bool:
    actor = _actor_membership(organization_id, context)
    workspace = _workspace_record(organization_id, workspace_id)
    _require_workspace_access(workspace, actor, "manager")
    record = EnterpriseWorkspaceCollaborator.query.filter_by(
        organization_id=organization_id,
        workspace_id=workspace_id,
        membership_id=membership_id,
    ).one_or_none()
    if record is None:
        return False
    collaborator_id = record.collaborator_id
    db.session.delete(record)
    _append_audit(
        organization_id,
        actor,
        "workspace.collaborator.removed",
        "workspace_collaborator",
        collaborator_id,
        {"membership_id": membership_id},
        workspace_id=workspace_id,
        now=now,
    )
    db.session.commit()
    return True


def save_decision(
    organization_id: str,
    workspace_id: str,
    payload: dict[str, Any],
    context: dict[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    membership = _actor_membership(organization_id, context)
    workspace = _workspace_record(organization_id, workspace_id)
    _require_workspace_access(workspace, membership, "editor")
    decision_payload = payload.get("payload")
    if not isinstance(decision_payload, dict):
        raise ValueError("decision payload must be a JSON object")
    payload_digest = _digest(decision_payload, "decision payload", MAX_DECISION_BYTES)
    operation = _text(payload.get("operation"), "decision operation", 80)
    title = _text(payload.get("title"), "decision title", 200)
    tags = _tags(payload.get("tags"))
    current = now or _utcnow()
    decision_days, _, _ = _policy_values(organization_id)
    decision_id = f"decision_{secrets.token_hex(10)}"
    record = EnterpriseSavedDecision(
        decision_id=decision_id,
        organization_id=organization_id,
        workspace_id=workspace_id,
        operation=operation,
        title=title,
        payload=decision_payload,
        payload_digest=payload_digest,
        tags=tags,
        status="active",
        created_by_membership_id=membership.membership_id,
        retained_until=current + timedelta(days=decision_days),
    )
    db.session.add(record)
    _append_audit(
        organization_id,
        membership,
        "decision.saved",
        "saved_decision",
        decision_id,
        {
            "operation": operation,
            "title": title,
            "payload_digest": payload_digest,
            "retention_days": decision_days,
        },
        workspace_id=workspace_id,
        now=current,
    )
    db.session.commit()
    return _decision_json(record)


def list_decisions(
    organization_id: str,
    workspace_id: str,
    context: dict[str, Any],
) -> list[dict[str, Any]]:
    membership = _actor_membership(organization_id, context)
    workspace = _workspace_record(organization_id, workspace_id)
    _require_workspace_access(workspace, membership, "viewer")
    records = (
        EnterpriseSavedDecision.query.filter_by(
            organization_id=organization_id,
            workspace_id=workspace_id,
        )
        .order_by(EnterpriseSavedDecision.created_at.desc())
        .all()
    )
    return [_decision_json(record) for record in records]


def save_report(
    organization_id: str,
    workspace_id: str,
    payload: dict[str, Any],
    context: dict[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    membership = _actor_membership(organization_id, context)
    workspace = _workspace_record(organization_id, workspace_id)
    _require_workspace_access(workspace, membership, "editor")
    content = payload.get("content")
    if not isinstance(content, dict):
        raise ValueError("report content must be a JSON object")
    content_digest = _digest(content, "report content", MAX_REPORT_BYTES)
    title = _text(payload.get("title"), "report title", 200)
    status = str(payload.get("status", "draft")).strip().lower()
    if status not in _REPORT_STATUSES:
        raise ValueError("report status must be draft, published, or archived")
    decision_ids = payload.get("decision_ids", [])
    if (
        not isinstance(decision_ids, list)
        or len(decision_ids) > MAX_REPORT_DECISIONS
        or len(set(decision_ids)) != len(decision_ids)
        or any(not isinstance(item, str) or not item for item in decision_ids)
    ):
        raise ValueError(f"decision_ids must contain at most {MAX_REPORT_DECISIONS} unique identifiers")
    if decision_ids:
        decisions = EnterpriseSavedDecision.query.filter(
            EnterpriseSavedDecision.decision_id.in_(decision_ids)
        ).all()
        valid_ids = {
            item.decision_id
            for item in decisions
            if item.organization_id == organization_id
            and item.workspace_id == workspace_id
            and item.status == "active"
        }
        if valid_ids != set(decision_ids):
            raise ValueError("reports can reference only active decisions in the same workspace")
    current = now or _utcnow()
    _, report_days, _ = _policy_values(organization_id)
    report_id = f"report_{secrets.token_hex(10)}"
    record = EnterpriseReport(
        report_id=report_id,
        organization_id=organization_id,
        workspace_id=workspace_id,
        title=title,
        content=content,
        content_digest=content_digest,
        decision_ids=decision_ids,
        status=status,
        created_by_membership_id=membership.membership_id,
        retained_until=current + timedelta(days=report_days),
    )
    db.session.add(record)
    _append_audit(
        organization_id,
        membership,
        "report.saved",
        "report",
        report_id,
        {
            "title": title,
            "status": status,
            "content_digest": content_digest,
            "decision_count": len(decision_ids),
            "retention_days": report_days,
        },
        workspace_id=workspace_id,
        now=current,
    )
    db.session.commit()
    return _report_json(record)


def list_reports(
    organization_id: str,
    workspace_id: str,
    context: dict[str, Any],
) -> list[dict[str, Any]]:
    membership = _actor_membership(organization_id, context)
    workspace = _workspace_record(organization_id, workspace_id)
    _require_workspace_access(workspace, membership, "viewer")
    records = (
        EnterpriseReport.query.filter_by(
            organization_id=organization_id,
            workspace_id=workspace_id,
        )
        .order_by(EnterpriseReport.created_at.desc())
        .all()
    )
    return [_report_json(record) for record in records]


def get_retention_policy(organization_id: str) -> dict[str, Any]:
    policy = db.session.get(EnterpriseRetentionPolicy, organization_id)
    if policy is None:
        return {
            "version": VERSION,
            "organization_id": organization_id,
            "decision_days": DEFAULT_DECISION_RETENTION_DAYS,
            "report_days": DEFAULT_REPORT_RETENTION_DAYS,
            "export_enabled": True,
            "persisted": False,
            "updated_by_membership_id": None,
            "updated_at": None,
        }
    return {
        "version": VERSION,
        "organization_id": organization_id,
        "decision_days": policy.decision_days,
        "report_days": policy.report_days,
        "export_enabled": policy.export_enabled,
        "persisted": True,
        "updated_by_membership_id": policy.updated_by_membership_id,
        "updated_at": _unix(policy.updated_at),
    }


def update_retention_policy(
    organization_id: str,
    payload: dict[str, Any],
    context: dict[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    membership = _actor_membership(organization_id, context)
    decision_days_value = payload.get("decision_days")
    report_days_value = payload.get("report_days")
    if (
        isinstance(decision_days_value, bool)
        or not isinstance(decision_days_value, int | str)
        or isinstance(report_days_value, bool)
        or not isinstance(report_days_value, int | str)
    ):
        raise ValueError("decision_days and report_days must be integers")
    try:
        decision_days = int(decision_days_value)
        report_days = int(report_days_value)
    except (TypeError, ValueError) as exc:
        raise ValueError("decision_days and report_days must be integers") from exc
    if not 1 <= decision_days <= MAX_RETENTION_DAYS:
        raise ValueError(f"decision_days must be between 1 and {MAX_RETENTION_DAYS}")
    if not 1 <= report_days <= MAX_RETENTION_DAYS:
        raise ValueError(f"report_days must be between 1 and {MAX_RETENTION_DAYS}")
    export_enabled = payload.get("export_enabled")
    if not isinstance(export_enabled, bool):
        raise ValueError("export_enabled must be a boolean")
    policy = db.session.get(EnterpriseRetentionPolicy, organization_id)
    if policy is None:
        policy = EnterpriseRetentionPolicy(
            organization_id=organization_id,
            decision_days=decision_days,
            report_days=report_days,
            export_enabled=export_enabled,
            updated_by_membership_id=membership.membership_id,
        )
        db.session.add(policy)
    else:
        policy.decision_days = decision_days
        policy.report_days = report_days
        policy.export_enabled = export_enabled
        policy.updated_by_membership_id = membership.membership_id
    active_decisions = EnterpriseSavedDecision.query.filter_by(
        organization_id=organization_id,
        status="active",
    ).all()
    active_reports = EnterpriseReport.query.filter(
        EnterpriseReport.organization_id == organization_id,
        EnterpriseReport.status != "expired",
    ).all()
    for record in active_decisions:
        record.retained_until = record.created_at + timedelta(days=decision_days)
    for record in active_reports:
        record.retained_until = record.created_at + timedelta(days=report_days)
    _append_audit(
        organization_id,
        membership,
        "retention.policy.updated",
        "retention_policy",
        organization_id,
        {
            "decision_days": decision_days,
            "report_days": report_days,
            "export_enabled": export_enabled,
            "updated_decisions": len(active_decisions),
            "updated_reports": len(active_reports),
        },
        now=now,
    )
    db.session.commit()
    return get_retention_policy(organization_id)


def apply_retention(
    organization_id: str,
    context: dict[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    membership = _actor_membership(organization_id, context)
    current = now or _utcnow()
    decisions = EnterpriseSavedDecision.query.filter(
        EnterpriseSavedDecision.organization_id == organization_id,
        EnterpriseSavedDecision.status == "active",
        EnterpriseSavedDecision.retained_until <= current,
    ).all()
    reports = EnterpriseReport.query.filter(
        EnterpriseReport.organization_id == organization_id,
        EnterpriseReport.status != "expired",
        EnterpriseReport.retained_until <= current,
    ).all()
    for record in decisions:
        record.payload = None
        record.status = "expired"
        record.expired_at = current
    for record in reports:
        record.content = None
        record.status = "expired"
        record.expired_at = current
    _append_audit(
        organization_id,
        membership,
        "retention.applied",
        "retention_run",
        f"retention_{secrets.token_hex(8)}",
        {
            "expired_decisions": len(decisions),
            "expired_reports": len(reports),
            "content_hard_deleted": False,
            "content_redacted": True,
        },
        now=current,
    )
    db.session.commit()
    return {
        "version": VERSION,
        "organization_id": organization_id,
        "applied_at": _unix(current),
        "expired_decisions": len(decisions),
        "expired_reports": len(reports),
        "content_hard_deleted": False,
        "content_redacted": True,
    }


def list_audit_events(
    organization_id: str,
    context: dict[str, Any],
    *,
    workspace_id: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    _actor_membership(organization_id, context)
    bounded_limit = max(1, min(int(limit), 500))
    all_records = (
        EnterpriseAuditEvent.query.filter_by(organization_id=organization_id)
        .order_by(EnterpriseAuditEvent.sequence.asc())
        .all()
    )
    chain_valid = True
    expected_previous = None
    for record in all_records:
        body = _audit_body(record)
        expected = _digest(body, "audit event", MAX_AUDIT_METADATA_BYTES + 4096)
        if record.previous_digest != expected_previous or record.event_digest != expected:
            chain_valid = False
            break
        expected_previous = record.event_digest
    selected = [
        record for record in all_records if workspace_id is None or record.workspace_id == workspace_id
    ][-bounded_limit:]
    return {
        "version": VERSION,
        "organization_id": organization_id,
        "workspace_id": workspace_id,
        "append_only": True,
        "chain_valid": chain_valid,
        "head_digest": expected_previous if chain_valid else None,
        "events": [_audit_json(record) for record in reversed(selected)],
    }


def export_workspace_data(
    organization_id: str,
    payload: dict[str, Any],
    context: dict[str, Any],
    *,
    include_audit: bool,
    now: datetime | None = None,
) -> dict[str, Any]:
    membership = _actor_membership(organization_id, context)
    _, _, export_enabled = _policy_values(organization_id)
    if not export_enabled:
        raise PermissionError("enterprise exports are disabled by retention policy")
    requested_workspace_id = payload.get("workspace_id")
    if requested_workspace_id is not None:
        requested_workspace_id = str(requested_workspace_id)
        workspace = _workspace_record(organization_id, requested_workspace_id)
        _require_workspace_access(workspace, membership, "viewer")
        workspaces = [workspace]
    else:
        visible_ids = {item["workspace_id"] for item in list_workspaces(organization_id, context)}
        workspaces = (
            EnterpriseWorkspace.query.filter(EnterpriseWorkspace.workspace_id.in_(visible_ids)).all()
            if visible_ids
            else []
        )
    workspace_ids = [workspace.workspace_id for workspace in workspaces]
    decisions = (
        EnterpriseSavedDecision.query.filter(EnterpriseSavedDecision.workspace_id.in_(workspace_ids))
        .limit(MAX_EXPORT_RECORDS + 1)
        .all()
        if workspace_ids
        else []
    )
    reports = (
        EnterpriseReport.query.filter(EnterpriseReport.workspace_id.in_(workspace_ids))
        .limit(MAX_EXPORT_RECORDS + 1)
        .all()
        if workspace_ids
        else []
    )
    if len(decisions) > MAX_EXPORT_RECORDS or len(reports) > MAX_EXPORT_RECORDS:
        raise ValueError(f"export exceeds the {MAX_EXPORT_RECORDS}-record limit")
    exported_at = now or _utcnow()
    export_id = f"export_{secrets.token_hex(10)}"
    audit = (
        list_audit_events(
            organization_id,
            context,
            workspace_id=requested_workspace_id,
            limit=500,
        )
        if include_audit
        else None
    )
    bundle = {
        "version": VERSION,
        "export_id": export_id,
        "organization_id": organization_id,
        "workspace_id": requested_workspace_id,
        "exported_at": _unix(exported_at),
        "content_redaction_preserved": True,
        "workspaces": [_workspace_json(item) for item in workspaces],
        "decisions": [_decision_json(item) for item in decisions],
        "reports": [_report_json(item) for item in reports],
        "audit": audit,
    }
    _append_audit(
        organization_id,
        membership,
        "export.created",
        "enterprise_export",
        export_id,
        {
            "workspace_id": requested_workspace_id,
            "include_audit": include_audit,
            "workspace_count": len(workspaces),
            "decision_count": len(decisions),
            "report_count": len(reports),
        },
        workspace_id=requested_workspace_id,
        now=exported_at,
    )
    db.session.commit()
    return bundle


def operations_manifest() -> dict[str, Any]:
    return {
        "version": VERSION,
        "features": {
            "tenant_scoped_workspaces": True,
            "collaborator_access_controls": True,
            "saved_decisions": True,
            "shared_reports": True,
            "append_only_audit": True,
            "hash_linked_audit_integrity": True,
            "json_exports": True,
            "retention_content_redaction": True,
            "retention_hard_delete": False,
        },
        "limits": {
            "decision_bytes": MAX_DECISION_BYTES,
            "report_bytes": MAX_REPORT_BYTES,
            "audit_metadata_bytes": MAX_AUDIT_METADATA_BYTES,
            "retention_days": MAX_RETENTION_DAYS,
            "export_records_per_type": MAX_EXPORT_RECORDS,
        },
    }
