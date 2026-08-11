"""Operational controls for the v4.5.2 delivery contract.

The v4.5.0 intake and v4.5.1 worker remain the owners of queue insertion and
outbound delivery.  This module adds tenant/workspace-scoped inspection,
replay, metrics, and enterprise audit integration without moving network work
onto the web process.
"""

from __future__ import annotations

import json
import time
from collections import Counter
from datetime import UTC, datetime
from typing import Any

from decision_delivery_v450 import _canonical

VERSION = "4.5.2"
MAX_LIMIT = 100
DEAD_LETTER_STATUSES = frozenset({"dead_letter", "failed"})
TERMINAL_STATUSES = frozenset({"delivered", "dead_letter", "failed"})


def _now() -> float:
    return round(time.time(), 6)


def _bounded_limit(limit: int | str | None) -> int:
    try:
        value = 50 if limit is None else int(limit)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"limit must be between 1 and {MAX_LIMIT}") from exc
    if not 1 <= value <= MAX_LIMIT:
        raise ValueError(f"limit must be between 1 and {MAX_LIMIT}")
    return value


def _backend_records(backend: Any, organization_id: str) -> list[dict[str, Any]]:
    """Read all currently indexed records for one organization."""
    client = getattr(backend, "client", None)
    prefix = getattr(backend, "key_prefix", None)
    if client is not None and prefix:
        ids = client.zrange(f"{prefix}:index:{organization_id}", 0, -1)
        records = []
        for delivery_id in ids:
            record = backend.get(organization_id, str(delivery_id))
            if record is not None:
                records.append(record)
    else:
        records = [
            dict(record)
            for record in getattr(backend, "_records", {}).values()
            if record.get("organization_id") == organization_id
        ]
    return sorted(
        records,
        key=lambda item: (float(item.get("updated_at") or 0), str(item.get("delivery_id"))),
        reverse=True,
    )


def _save_replay(backend: Any, record: dict[str, Any]) -> None:
    client = getattr(backend, "client", None)
    prefix = getattr(backend, "key_prefix", None)
    if client is not None and prefix:
        client.zrem(f"{prefix}:retry", record["delivery_id"])
        client.set(
            f"{prefix}:status:{record['delivery_id']}",
            _canonical(record),
            ex=getattr(backend, "ttl_seconds", None),
        )
        client.xadd(
            f"{prefix}:stream",
            {"delivery": _canonical(record)},
            maxlen=10000,
            approximate=True,
        )
        return
    records = getattr(backend, "_records", None)
    if records is None:
        raise RuntimeError("delivery backend does not support replay")
    records[record["delivery_id"]] = record


def _workspace_visible(
    organization_id: str,
    workspace_id: str | None,
    context: dict[str, Any],
) -> None:
    if workspace_id is None:
        return
    from enterprise_operations_v443 import list_workspaces

    visible = list_workspaces(organization_id, context)
    if not any(item.get("workspace_id") == workspace_id for item in visible):
        raise PermissionError("workspace is not visible to the authenticated principal")


def list_dead_letters(
    backend: Any,
    organization_id: str,
    *,
    limit: int | str | None = None,
    workspace_id: str | None = None,
) -> list[dict[str, Any]]:
    bounded = _bounded_limit(limit)
    result = []
    for record in _backend_records(backend, organization_id):
        if record.get("status") not in DEAD_LETTER_STATUSES:
            continue
        if workspace_id is not None and record.get("workspace_id") != workspace_id:
            continue
        item = dict(record)
        item["dead_letter"] = True
        if item.get("status") == "failed":
            item["legacy_status"] = "failed"
            item["status"] = "dead_letter"
        result.append(item)
    return result[:bounded]


def replay_delivery(
    backend: Any,
    organization_id: str,
    delivery_id: str,
    *,
    context: dict[str, Any],
    workspace_id: str | None = None,
) -> dict[str, Any]:
    record = backend.get(organization_id, delivery_id)
    if record is None:
        raise KeyError("delivery not found")
    record_workspace = record.get("workspace_id")
    if workspace_id is not None and record_workspace != workspace_id:
        raise KeyError("delivery not found")
    _workspace_visible(organization_id, workspace_id or record_workspace, context)
    if record.get("status") not in TERMINAL_STATUSES:
        raise ValueError("only terminal deliveries can be replayed")
    now = _now()
    record.update(
        {
            "status": "queued",
            "attempts": 0,
            "updated_at": now,
            "replayed_at": now,
            "replay_count": int(record.get("replay_count") or 0) + 1,
            "next_attempt_at": None,
            "last_error": None,
            "response_status": None,
            "failed_at": None,
            "dead_letter_at": None,
        }
    )
    _save_replay(backend, record)
    return {**record, "replayed": True}


def delivery_metrics(
    backend: Any,
    organization_id: str,
    *,
    workspace_id: str | None = None,
) -> dict[str, Any]:
    records = _backend_records(backend, organization_id)
    if workspace_id is not None:
        records = [item for item in records if item.get("workspace_id") == workspace_id]
    statuses = Counter(str(item.get("status") or "unknown") for item in records)
    attempts = sum(int(item.get("attempts") or 0) for item in records)
    return {
        "version": VERSION,
        "organization_id": organization_id,
        "workspace_id": workspace_id,
        "generated_at": _now(),
        "delivery_count": len(records),
        "attempt_count": attempts,
        "status_counts": {key: statuses.get(key, 0) for key in (
            "queued", "dispatching", "retrying", "delivered", "failed", "dead_letter"
        )},
        "dead_letter_count": sum(1 for item in records if item.get("status") in DEAD_LETTER_STATUSES),
    }


def audit_delivery_event(
    organization_id: str,
    context: dict[str, Any],
    action: str,
    resource_type: str,
    resource_id: str,
    metadata: dict[str, Any],
    *,
    workspace_id: str | None = None,
) -> None:
    """Append a delivery operation to the existing enterprise hash chain."""
    from database import db
    from enterprise_operations_v443 import _append_audit, _actor_membership

    membership = _actor_membership(organization_id, context)
    _append_audit(
        organization_id,
        membership,
        action,
        resource_type,
        resource_id,
        metadata,
        workspace_id=workspace_id,
        now=datetime.now(UTC),
    )
    db.session.commit()


def ensure_workspace_scope(
    organization_id: str,
    workspace_id: str | None,
    context: dict[str, Any],
) -> None:
    _workspace_visible(organization_id, workspace_id, context)


