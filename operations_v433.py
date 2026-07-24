"""Persistent lifecycle operations for NFL Analytics Hub v4.3.3."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import threading
import time
from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import Any

from lifecycle_v43 import normalize_model_version, transition_model_version

VERSION = "4.3.3"
REGISTRY_CONTRACT_VERSION = "4.3.0"
MAX_METADATA_BYTES = 256 * 1024
MAX_AUDIT_EVENTS = 2_000
MAX_ALERTS = 1_000
MAX_APPROVALS = 1_000
CONTROLLED_TRANSITIONS = {"champion", "retired", "archived"}

_IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9._-]{0,119}$")
_MODEL_VERSION_ID = re.compile(r"^mv_[a-f0-9]{20}$")
_SHA256 = re.compile(r"^(?:sha256:)?([a-f0-9]{64})$")
_APPROVAL_ACTIONS = {
    "model.lifecycle.transition",
    "model.retraining.request",
    "model.rollout.advance",
    "model.rollout.rollback",
}
_SEVERITIES = {"info", "warning", "critical"}


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


def _integer(value: Any, field: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be an integer")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be an integer") from exc
    if result < minimum or result > maximum:
        raise ValueError(f"{field} must be between {minimum} and {maximum}")
    return result


def _identifier(value: Any, field: str) -> str:
    result = str(value or "").strip().lower()
    if not _IDENTIFIER.fullmatch(result):
        raise ValueError(f"{field} must use 1-120 lowercase letters, numbers, dots, dashes, or underscores")
    return result


def _model_version_id(value: Any) -> str:
    result = str(value or "").strip().lower()
    if not _MODEL_VERSION_ID.fullmatch(result):
        raise ValueError("model_version_id must be a normalized v4.3 identity")
    return result


def _text(value: Any, field: str, maximum: int) -> str:
    result = str(value or "").strip()
    if not result:
        raise ValueError(f"{field} is required")
    if len(result) > maximum:
        raise ValueError(f"{field} cannot exceed {maximum} characters")
    return result


def _sha256(value: Any, field: str) -> str:
    text = _text(value, field, 71).lower()
    match = _SHA256.fullmatch(text)
    if not match:
        raise ValueError(f"{field} must be a SHA-256 digest")
    return f"sha256:{match.group(1)}"


def _boolean(value: Any, field: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{field} must be a boolean")
    return value


def approval_evidence_for_transition(
    model_version: Mapping[str, Any],
    target_status: Any,
    promotion_decision: Any = None,
) -> str:
    """Return the exact evidence digest an approval must bind to."""
    target = str(target_status or "").strip().lower()
    if target == "champion":
        if not isinstance(promotion_decision, Mapping):
            raise ValueError("promotion decision is required when a model becomes champion")
        return _sha256(
            promotion_decision.get("evidence_digest"),
            "promotion decision evidence_digest",
        )
    return _sha256(model_version.get("metadata_digest"), "model metadata_digest")


def normalize_approval_request(
    payload: Mapping[str, Any],
    *,
    requested_at: float | None = None,
) -> dict[str, Any]:
    """Normalize an approval bound to one exact action, resource, and evidence digest."""
    if not isinstance(payload, Mapping):
        raise ValueError("approval request must be a JSON object")
    action = _identifier(payload.get("action"), "action")
    if action not in _APPROVAL_ACTIONS:
        raise ValueError("action is not approval-controlled")
    target_status = None
    if action == "model.lifecycle.transition":
        target_status = _identifier(payload.get("target_status"), "target_status")
        if target_status not in CONTROLLED_TRANSITIONS:
            raise ValueError("target_status is not an approval-controlled transition")
    resource_id = _identifier(payload.get("resource_id"), "resource_id")
    evidence_digest = _sha256(payload.get("evidence_digest"), "evidence_digest")
    requester = _identifier(payload.get("requested_by"), "requested_by")
    timestamp = _timestamp(
        time.time() if requested_at is None else requested_at,
        "requested_at",
    )
    expires_at = _timestamp(
        payload.get("expires_at", timestamp + 24 * 60 * 60),
        "expires_at",
    )
    if expires_at <= timestamp:
        raise ValueError("expires_at must follow requested_at")
    if expires_at - timestamp > 7 * 24 * 60 * 60:
        raise ValueError("approval cannot remain open for more than 7 days")
    body = {
        "action": action,
        "target_status": target_status,
        "resource_id": resource_id,
        "evidence_digest": evidence_digest,
        "requested_by": requester,
        "reason": _text(payload.get("reason"), "reason", 500),
        "requested_at": timestamp,
        "expires_at": expires_at,
    }
    approval_id = hashlib.sha256(_canonical_json(body, "approval request", 64 * 1024).encode()).hexdigest()[
        :24
    ]
    return {
        "version": VERSION,
        "approval_id": f"approval_{approval_id}",
        **body,
        "status": "pending",
        "decided_by": None,
        "decision_reason": None,
        "decided_at": None,
    }


def decide_approval(
    approval: Mapping[str, Any],
    decision: Any,
    *,
    decided_by: Any,
    reason: Any,
    decided_at: float | None = None,
) -> dict[str, Any]:
    """Apply a four-eyes approval decision without executing the approved action."""
    if not isinstance(approval, Mapping) or approval.get("version") != VERSION:
        raise ValueError(f"approval must use version {VERSION}")
    if approval.get("status") != "pending":
        raise ValueError("approval is already decided")
    normalized_decision = str(decision or "").strip().lower()
    if normalized_decision not in {"approved", "rejected"}:
        raise ValueError("decision must be approved or rejected")
    actor = _identifier(decided_by, "decided_by")
    if actor == approval.get("requested_by"):
        raise ValueError("approver must differ from requester")
    timestamp = _timestamp(
        time.time() if decided_at is None else decided_at,
        "decided_at",
    )
    if timestamp < _timestamp(approval.get("requested_at"), "requested_at"):
        raise ValueError("decided_at cannot precede requested_at")
    if timestamp > _timestamp(approval.get("expires_at"), "expires_at"):
        raise ValueError("approval has expired")
    updated = deepcopy(dict(approval))
    updated.update(
        {
            "status": normalized_decision,
            "decided_by": actor,
            "decision_reason": _text(reason, "decision reason", 500),
            "decided_at": timestamp,
        }
    )
    return updated


def validate_approval(
    approval: Mapping[str, Any],
    *,
    action: str,
    resource_id: str,
    evidence_digest: str,
    at: float,
    target_status: str | None = None,
) -> None:
    if not isinstance(approval, Mapping) or approval.get("version") != VERSION:
        raise ValueError("a v4.3.3 approval is required")
    if approval.get("status") != "approved":
        raise ValueError("approval must be approved")
    if approval.get("action") != action:
        raise ValueError("approval action does not match")
    if approval.get("resource_id") != resource_id:
        raise ValueError("approval resource does not match")
    if approval.get("target_status") != target_status:
        raise ValueError("approval target_status does not match")
    if approval.get("evidence_digest") != evidence_digest:
        raise ValueError("approval evidence does not match")
    if _timestamp(approval.get("expires_at"), "expires_at") < at:
        raise ValueError("approval has expired")


def normalize_health_observation(
    payload: Mapping[str, Any],
    *,
    observed_at: float | None = None,
) -> dict[str, Any]:
    """Normalize caller-supplied lifecycle health and emit deterministic alerts."""
    if not isinstance(payload, Mapping):
        raise ValueError("health observation must be a JSON object")
    model_version_id = _model_version_id(payload.get("model_version_id"))
    timestamp = _timestamp(
        time.time() if observed_at is None else observed_at,
        "observed_at",
    )
    checks = payload.get("checks")
    if isinstance(checks, str | bytes) or not isinstance(checks, Sequence):
        raise ValueError("checks must be a list")
    if not checks or len(checks) > 20:
        raise ValueError("checks must contain between 1 and 20 items")
    normalized_checks: list[dict[str, Any]] = []
    names: set[str] = set()
    alerts: list[dict[str, Any]] = []
    for item in checks:
        if not isinstance(item, Mapping):
            raise ValueError("each health check must be a JSON object")
        name = _identifier(item.get("name"), "health check name")
        if name in names:
            raise ValueError(f"checks contains duplicate check {name}")
        names.add(name)
        healthy = _boolean(item.get("healthy"), f"health check {name} healthy")
        severity = str(item.get("severity", "warning")).strip().lower()
        if severity not in _SEVERITIES:
            raise ValueError(f"health check {name} severity is unsupported")
        check_observed_at = _timestamp(
            item.get("observed_at", timestamp),
            f"health check {name} observed_at",
        )
        if check_observed_at > timestamp:
            raise ValueError(f"health check {name} observed_at cannot be in the future")
        maximum_age = _integer(
            item.get("maximum_age_seconds", 15 * 60),
            f"health check {name} maximum_age_seconds",
            1,
            7 * 24 * 60 * 60,
        )
        stale = timestamp - check_observed_at > maximum_age
        normalized = {
            "name": name,
            "healthy": healthy,
            "severity": severity,
            "observed_at": check_observed_at,
            "maximum_age_seconds": maximum_age,
            "stale": stale,
            "evidence_digest": _sha256(
                item.get("evidence_digest"),
                f"health check {name} evidence_digest",
            ),
            "detail": _text(item.get("detail", name), f"health check {name} detail", 500),
        }
        normalized_checks.append(normalized)
        if not healthy or stale:
            alert_body = {
                "model_version_id": model_version_id,
                "check": name,
                "reason": "stale" if stale else "threshold-breach",
                "severity": severity,
                "evidence_digest": normalized["evidence_digest"],
                "observed_at": timestamp,
            }
            alert_id = hashlib.sha256(
                _canonical_json(alert_body, "lifecycle alert", 64 * 1024).encode()
            ).hexdigest()[:24]
            alerts.append(
                {
                    "version": VERSION,
                    "alert_id": f"lifecycle_alert_{alert_id}",
                    "alert_type": "model.lifecycle.health",
                    **alert_body,
                    "detail": normalized["detail"],
                }
            )
    normalized_checks.sort(key=lambda item: item["name"])
    body = {
        "model_version_id": model_version_id,
        "observed_at": timestamp,
        "checks": normalized_checks,
    }
    return {
        "version": VERSION,
        "health_id": f"health_{hashlib.sha256(_canonical_json(body, 'health').encode()).hexdigest()[:24]}",
        **body,
        "healthy": not alerts,
        "alerts": sorted(alerts, key=lambda item: item["alert_id"]),
    }


def _audit_event(
    action: str,
    resource_id: str,
    actor: str,
    details: Mapping[str, Any],
    *,
    occurred_at: float,
    sequence: int,
) -> dict[str, Any]:
    safe_details = json.loads(_canonical_json(details, "audit details", 64 * 1024))
    body = {
        "action": _identifier(action, "audit action"),
        "resource_id": _identifier(resource_id, "audit resource_id"),
        "actor": _identifier(actor, "audit actor"),
        "details": safe_details,
        "occurred_at": _timestamp(occurred_at, "audit occurred_at"),
        "sequence": _integer(sequence, "audit sequence", 1, 1_000_000_000),
    }
    event_id = hashlib.sha256(_canonical_json(body, "audit event", 128 * 1024).encode()).hexdigest()[:24]
    return {
        "version": VERSION,
        "audit_id": f"audit_{event_id}",
        "event_type": "model.lifecycle.audit",
        **body,
    }


class InMemoryLifecycleOperations:
    """Thread-safe development adapter matching the persistent operations contract."""

    backend = "memory"
    durable = False

    def __init__(self) -> None:
        self._versions: dict[str, dict[str, Any]] = {}
        self._approvals: dict[str, dict[str, Any]] = {}
        self._health: dict[str, dict[str, Any]] = {}
        self._alerts: list[dict[str, Any]] = []
        self._audit: list[dict[str, Any]] = []
        self._lock = threading.RLock()

    def register(
        self,
        payload: Mapping[str, Any],
        *,
        registered_at: float | None = None,
    ) -> dict[str, Any]:
        candidate = normalize_model_version(payload, registered_at=registered_at)
        identity = candidate["model_version_id"]
        with self._lock:
            existing = self._versions.get(identity)
            if existing is not None:
                if existing["metadata_digest"] != candidate["metadata_digest"]:
                    raise ValueError("model key and version conflict with existing metadata")
                return {"accepted": False, "deduplicated": True, "model_version": deepcopy(existing)}
            self._versions[identity] = candidate
            self._append_audit(
                "model.version.registered",
                identity,
                candidate["registered_by"],
                {"metadata_digest": candidate["metadata_digest"]},
                candidate["registered_at"],
            )
            return {"accepted": True, "deduplicated": False, "model_version": deepcopy(candidate)}

    def get(self, model_version_id: Any) -> dict[str, Any] | None:
        identity = _model_version_id(model_version_id)
        with self._lock:
            record = self._versions.get(identity)
            return deepcopy(record) if record is not None else None

    def list_versions(
        self,
        *,
        model_key: Any = None,
        status: Any = None,
        limit: Any = 100,
    ) -> list[dict[str, Any]]:
        bounded = _integer(limit, "limit", 1, 500)
        normalized_key = None if model_key is None else _identifier(model_key, "model_key")
        normalized_status = None if status is None else _identifier(status, "status")
        with self._lock:
            records = [
                deepcopy(item)
                for item in self._versions.values()
                if (normalized_key is None or item["model_key"] == normalized_key)
                and (normalized_status is None or item["status"] == normalized_status)
            ]
        return sorted(
            records,
            key=lambda item: (item["updated_at"], item["model_version_id"]),
            reverse=True,
        )[:bounded]

    def transition(
        self,
        model_version_id: Any,
        target_status: Any,
        *,
        actor: Any,
        reason: Any,
        occurred_at: float | None = None,
        promotion_decision: Any = None,
        approval_id: Any = None,
    ) -> dict[str, Any]:
        identity = _model_version_id(model_version_id)
        target = str(target_status or "").strip().lower()
        timestamp = _timestamp(
            time.time() if occurred_at is None else occurred_at,
            "occurred_at",
        )
        normalized_actor = _identifier(actor, "actor")
        with self._lock:
            current = self._versions.get(identity)
            if current is None:
                raise KeyError("model version not found")
            approval = None
            if target in CONTROLLED_TRANSITIONS:
                approval = self._approval_for_transition(
                    approval_id,
                    current,
                    target,
                    promotion_decision,
                    timestamp,
                )
            updated = transition_model_version(
                current,
                target,
                occurred_at=timestamp,
                actor=normalized_actor,
                reason=reason,
                promotion_decision=promotion_decision,
            )
            self._versions[identity] = updated
            self._append_audit(
                "model.lifecycle.transitioned",
                identity,
                normalized_actor,
                {
                    "from_status": current["status"],
                    "to_status": target,
                    "lifecycle_event_id": updated["history"][-1]["event_id"],
                    "approval_id": None if approval is None else approval["approval_id"],
                },
                timestamp,
            )
            return deepcopy(updated)

    def request_approval(
        self,
        payload: Mapping[str, Any],
        *,
        requested_at: float | None = None,
    ) -> dict[str, Any]:
        approval = normalize_approval_request(payload, requested_at=requested_at)
        identity = approval["approval_id"]
        with self._lock:
            existing = self._approvals.get(identity)
            if existing is not None:
                return {"accepted": False, "deduplicated": True, "approval": deepcopy(existing)}
            if len(self._approvals) >= MAX_APPROVALS:
                raise RuntimeError("approval capacity reached")
            self._approvals[identity] = approval
            self._append_audit(
                "model.approval.requested",
                approval["resource_id"],
                approval["requested_by"],
                {"approval_id": identity, "action": approval["action"]},
                approval["requested_at"],
            )
            return {"accepted": True, "deduplicated": False, "approval": deepcopy(approval)}

    def decide_approval(
        self,
        approval_id: Any,
        decision: Any,
        *,
        decided_by: Any,
        reason: Any,
        decided_at: float | None = None,
    ) -> dict[str, Any]:
        identity = _identifier(approval_id, "approval_id")
        with self._lock:
            current = self._approvals.get(identity)
            if current is None:
                raise KeyError("approval not found")
            updated = decide_approval(
                current,
                decision,
                decided_by=decided_by,
                reason=reason,
                decided_at=decided_at,
            )
            self._approvals[identity] = updated
            self._append_audit(
                "model.approval.decided",
                updated["resource_id"],
                updated["decided_by"],
                {"approval_id": identity, "decision": updated["status"]},
                updated["decided_at"],
            )
            return deepcopy(updated)

    def list_approvals(self, *, status: Any = None, limit: Any = 100) -> list[dict[str, Any]]:
        bounded = _integer(limit, "limit", 1, 500)
        normalized_status = None if status is None else _identifier(status, "status")
        with self._lock:
            records = [
                deepcopy(item)
                for item in self._approvals.values()
                if normalized_status is None or item["status"] == normalized_status
            ]
        return sorted(
            records,
            key=lambda item: (item["requested_at"], item["approval_id"]),
            reverse=True,
        )[:bounded]

    def record_health(
        self,
        payload: Mapping[str, Any],
        *,
        observed_at: float | None = None,
        actor: Any,
    ) -> dict[str, Any]:
        observation = normalize_health_observation(payload, observed_at=observed_at)
        normalized_actor = _identifier(actor, "actor")
        identity = observation["model_version_id"]
        with self._lock:
            if identity not in self._versions:
                raise KeyError("model version not found")
            self._health[identity] = observation
            known = {item["alert_id"] for item in self._alerts}
            self._alerts.extend(alert for alert in observation["alerts"] if alert["alert_id"] not in known)
            self._alerts = self._alerts[-MAX_ALERTS:]
            self._append_audit(
                "model.health.observed",
                identity,
                normalized_actor,
                {
                    "health_id": observation["health_id"],
                    "healthy": observation["healthy"],
                    "alert_ids": [item["alert_id"] for item in observation["alerts"]],
                },
                observation["observed_at"],
            )
            return deepcopy(observation)

    def audit_history(self, *, resource_id: Any = None, limit: Any = 100) -> list[dict[str, Any]]:
        bounded = _integer(limit, "limit", 1, 500)
        normalized_resource = None if resource_id is None else _identifier(resource_id, "resource_id")
        with self._lock:
            records = [
                deepcopy(item)
                for item in reversed(self._audit)
                if normalized_resource is None or item["resource_id"] == normalized_resource
            ]
        return records[:bounded]

    def operations_snapshot(self) -> dict[str, Any]:
        with self._lock:
            statuses: dict[str, int] = {}
            for item in self._versions.values():
                statuses[item["status"]] = statuses.get(item["status"], 0) + 1
            return {
                "version": VERSION,
                "backend": self.backend,
                "durable": self.durable,
                "healthy": True,
                "registry": {"total": len(self._versions), "by_status": statuses},
                "approvals": {
                    "pending": sum(item["status"] == "pending" for item in self._approvals.values()),
                    "total": len(self._approvals),
                },
                "health": {
                    "observed_models": len(self._health),
                    "unhealthy_models": sum(not item["healthy"] for item in self._health.values()),
                    "alerts": len(self._alerts),
                },
                "audit_events": len(self._audit),
            }

    def _approval_for_transition(
        self,
        approval_id: Any,
        current: Mapping[str, Any],
        target: str,
        promotion_decision: Any,
        timestamp: float,
    ) -> dict[str, Any]:
        if approval_id is None:
            raise ValueError("controlled transition requires an approval")
        identity = _identifier(approval_id, "approval_id")
        approval = self._approvals.get(identity)
        if approval is None:
            raise ValueError("controlled transition requires an approval")
        validate_approval(
            approval,
            action="model.lifecycle.transition",
            resource_id=current["model_version_id"],
            evidence_digest=approval_evidence_for_transition(
                current,
                target,
                promotion_decision,
            ),
            at=timestamp,
            target_status=target,
        )
        return approval

    def _append_audit(
        self,
        action: str,
        resource_id: str,
        actor: str,
        details: Mapping[str, Any],
        occurred_at: float,
    ) -> None:
        self._audit.append(
            _audit_event(
                action,
                resource_id,
                actor,
                details,
                occurred_at=occurred_at,
                sequence=len(self._audit) + 1,
            )
        )
        self._audit = self._audit[-MAX_AUDIT_EVENTS:]


class RedisLifecycleOperations(InMemoryLifecycleOperations):
    """Redis-backed lifecycle adapter with optimistic transition locking."""

    backend = "redis"
    durable = True

    def __init__(self, redis_client: Any, *, key_prefix: str = "nfl:v43") -> None:
        super().__init__()
        self.redis = redis_client
        self.key_prefix = _text(key_prefix, "key_prefix", 128).rstrip(":")

    @property
    def _versions_key(self) -> str:
        return f"{self.key_prefix}:versions"

    @property
    def _approvals_key(self) -> str:
        return f"{self.key_prefix}:approvals"

    @property
    def _health_key(self) -> str:
        return f"{self.key_prefix}:health"

    @property
    def _alerts_key(self) -> str:
        return f"{self.key_prefix}:alerts"

    @property
    def _alert_ids_key(self) -> str:
        return f"{self.key_prefix}:alert-ids"

    @property
    def _audit_key(self) -> str:
        return f"{self.key_prefix}:audit"

    def register(
        self,
        payload: Mapping[str, Any],
        *,
        registered_at: float | None = None,
    ) -> dict[str, Any]:
        candidate = normalize_model_version(payload, registered_at=registered_at)
        identity = candidate["model_version_id"]
        raw = _canonical_json(candidate, "model version")
        created = self.redis.hsetnx(self._versions_key, identity, raw)
        if not created:
            existing = self.get(identity)
            if existing is None:
                raise RuntimeError("registry write could not be read")
            if existing["metadata_digest"] != candidate["metadata_digest"]:
                raise ValueError("model key and version conflict with existing metadata")
            return {"accepted": False, "deduplicated": True, "model_version": existing}
        self._append_redis_audit(
            "model.version.registered",
            identity,
            candidate["registered_by"],
            {"metadata_digest": candidate["metadata_digest"]},
            candidate["registered_at"],
        )
        return {"accepted": True, "deduplicated": False, "model_version": candidate}

    def get(self, model_version_id: Any) -> dict[str, Any] | None:
        raw = self.redis.hget(self._versions_key, _model_version_id(model_version_id))
        return None if raw is None else json.loads(raw)

    def list_versions(
        self,
        *,
        model_key: Any = None,
        status: Any = None,
        limit: Any = 100,
    ) -> list[dict[str, Any]]:
        bounded = _integer(limit, "limit", 1, 500)
        normalized_key = None if model_key is None else _identifier(model_key, "model_key")
        normalized_status = None if status is None else _identifier(status, "status")
        records = [json.loads(raw) for raw in self.redis.hvals(self._versions_key)]
        filtered = [
            item
            for item in records
            if (normalized_key is None or item["model_key"] == normalized_key)
            and (normalized_status is None or item["status"] == normalized_status)
        ]
        return sorted(
            filtered,
            key=lambda item: (item["updated_at"], item["model_version_id"]),
            reverse=True,
        )[:bounded]

    def transition(
        self,
        model_version_id: Any,
        target_status: Any,
        *,
        actor: Any,
        reason: Any,
        occurred_at: float | None = None,
        promotion_decision: Any = None,
        approval_id: Any = None,
    ) -> dict[str, Any]:
        from redis.exceptions import WatchError

        identity = _model_version_id(model_version_id)
        target = str(target_status or "").strip().lower()
        timestamp = _timestamp(
            time.time() if occurred_at is None else occurred_at,
            "occurred_at",
        )
        normalized_actor = _identifier(actor, "actor")
        for _ in range(5):
            with self.redis.pipeline() as pipe:
                try:
                    pipe.watch(self._versions_key)
                    raw = pipe.hget(self._versions_key, identity)
                    if raw is None:
                        raise KeyError("model version not found")
                    current = json.loads(raw)
                    approval = None
                    if target in CONTROLLED_TRANSITIONS:
                        approval = self._redis_approval_for_transition(
                            approval_id,
                            current,
                            target,
                            promotion_decision,
                            timestamp,
                        )
                    updated = transition_model_version(
                        current,
                        target,
                        occurred_at=timestamp,
                        actor=normalized_actor,
                        reason=reason,
                        promotion_decision=promotion_decision,
                    )
                    pipe.multi()
                    pipe.hset(
                        self._versions_key,
                        identity,
                        _canonical_json(updated, "model version"),
                    )
                    pipe.execute()
                    self._append_redis_audit(
                        "model.lifecycle.transitioned",
                        identity,
                        normalized_actor,
                        {
                            "from_status": current["status"],
                            "to_status": target,
                            "lifecycle_event_id": updated["history"][-1]["event_id"],
                            "approval_id": (None if approval is None else approval["approval_id"]),
                        },
                        timestamp,
                    )
                    return updated
                except WatchError:
                    continue
        raise RuntimeError("model version changed concurrently; retry the transition")

    def request_approval(
        self,
        payload: Mapping[str, Any],
        *,
        requested_at: float | None = None,
    ) -> dict[str, Any]:
        approval = normalize_approval_request(payload, requested_at=requested_at)
        identity = approval["approval_id"]
        existing = self.redis.hget(self._approvals_key, identity)
        if existing is not None:
            return {
                "accepted": False,
                "deduplicated": True,
                "approval": json.loads(existing),
            }
        if self.redis.hlen(self._approvals_key) >= MAX_APPROVALS:
            raise RuntimeError("approval capacity reached")
        created = self.redis.hsetnx(
            self._approvals_key,
            identity,
            _canonical_json(approval, "approval"),
        )
        if not created:
            current = self._get_approval(identity)
            return {"accepted": False, "deduplicated": True, "approval": current}
        self._append_redis_audit(
            "model.approval.requested",
            approval["resource_id"],
            approval["requested_by"],
            {"approval_id": identity, "action": approval["action"]},
            approval["requested_at"],
        )
        return {"accepted": True, "deduplicated": False, "approval": approval}

    def decide_approval(
        self,
        approval_id: Any,
        decision: Any,
        *,
        decided_by: Any,
        reason: Any,
        decided_at: float | None = None,
    ) -> dict[str, Any]:
        from redis.exceptions import WatchError

        identity = _identifier(approval_id, "approval_id")
        for _ in range(5):
            with self.redis.pipeline() as pipe:
                try:
                    pipe.watch(self._approvals_key)
                    raw = pipe.hget(self._approvals_key, identity)
                    if raw is None:
                        raise KeyError("approval not found")
                    updated = decide_approval(
                        json.loads(raw),
                        decision,
                        decided_by=decided_by,
                        reason=reason,
                        decided_at=decided_at,
                    )
                    pipe.multi()
                    pipe.hset(
                        self._approvals_key,
                        identity,
                        _canonical_json(updated, "approval"),
                    )
                    pipe.execute()
                    self._append_redis_audit(
                        "model.approval.decided",
                        updated["resource_id"],
                        updated["decided_by"],
                        {"approval_id": identity, "decision": updated["status"]},
                        updated["decided_at"],
                    )
                    return updated
                except WatchError:
                    continue
        raise RuntimeError("approval changed concurrently; retry the decision")

    def list_approvals(self, *, status: Any = None, limit: Any = 100) -> list[dict[str, Any]]:
        bounded = _integer(limit, "limit", 1, 500)
        normalized_status = None if status is None else _identifier(status, "status")
        records = [json.loads(raw) for raw in self.redis.hvals(self._approvals_key)]
        filtered = [
            item for item in records if normalized_status is None or item["status"] == normalized_status
        ]
        return sorted(
            filtered,
            key=lambda item: (item["requested_at"], item["approval_id"]),
            reverse=True,
        )[:bounded]

    def record_health(
        self,
        payload: Mapping[str, Any],
        *,
        observed_at: float | None = None,
        actor: Any,
    ) -> dict[str, Any]:
        observation = normalize_health_observation(payload, observed_at=observed_at)
        identity = observation["model_version_id"]
        if self.get(identity) is None:
            raise KeyError("model version not found")
        normalized_actor = _identifier(actor, "actor")
        with self.redis.pipeline() as pipe:
            pipe.hset(
                self._health_key,
                identity,
                _canonical_json(observation, "health observation"),
            )
            for alert in observation["alerts"]:
                if not self.redis.sismember(self._alert_ids_key, alert["alert_id"]):
                    pipe.sadd(self._alert_ids_key, alert["alert_id"])
                    pipe.lpush(self._alerts_key, _canonical_json(alert, "lifecycle alert"))
            pipe.ltrim(self._alerts_key, 0, MAX_ALERTS - 1)
            pipe.execute()
        self._append_redis_audit(
            "model.health.observed",
            identity,
            normalized_actor,
            {
                "health_id": observation["health_id"],
                "healthy": observation["healthy"],
                "alert_ids": [item["alert_id"] for item in observation["alerts"]],
            },
            observation["observed_at"],
        )
        return observation

    def audit_history(self, *, resource_id: Any = None, limit: Any = 100) -> list[dict[str, Any]]:
        bounded = _integer(limit, "limit", 1, 500)
        normalized_resource = None if resource_id is None else _identifier(resource_id, "resource_id")
        records = [json.loads(raw) for raw in self.redis.lrange(self._audit_key, 0, MAX_AUDIT_EVENTS - 1)]
        return [
            item
            for item in records
            if normalized_resource is None or item["resource_id"] == normalized_resource
        ][:bounded]

    def operations_snapshot(self) -> dict[str, Any]:
        self.redis.ping()
        versions = [json.loads(raw) for raw in self.redis.hvals(self._versions_key)]
        approvals = [json.loads(raw) for raw in self.redis.hvals(self._approvals_key)]
        health = [json.loads(raw) for raw in self.redis.hvals(self._health_key)]
        statuses: dict[str, int] = {}
        for item in versions:
            statuses[item["status"]] = statuses.get(item["status"], 0) + 1
        return {
            "version": VERSION,
            "backend": self.backend,
            "durable": self.durable,
            "healthy": True,
            "registry": {"total": len(versions), "by_status": statuses},
            "approvals": {
                "pending": sum(item["status"] == "pending" for item in approvals),
                "total": len(approvals),
            },
            "health": {
                "observed_models": len(health),
                "unhealthy_models": sum(not item["healthy"] for item in health),
                "alerts": self.redis.llen(self._alerts_key),
            },
            "audit_events": self.redis.llen(self._audit_key),
        }

    def _get_approval(self, approval_id: str) -> dict[str, Any]:
        raw = self.redis.hget(self._approvals_key, approval_id)
        if raw is None:
            raise KeyError("approval not found")
        return json.loads(raw)

    def _redis_approval_for_transition(
        self,
        approval_id: Any,
        current: Mapping[str, Any],
        target: str,
        promotion_decision: Any,
        timestamp: float,
    ) -> dict[str, Any]:
        if approval_id is None:
            raise ValueError("controlled transition requires an approval")
        approval = self._get_approval(_identifier(approval_id, "approval_id"))
        validate_approval(
            approval,
            action="model.lifecycle.transition",
            resource_id=current["model_version_id"],
            evidence_digest=approval_evidence_for_transition(
                current,
                target,
                promotion_decision,
            ),
            at=timestamp,
            target_status=target,
        )
        return approval

    def _append_redis_audit(
        self,
        action: str,
        resource_id: str,
        actor: str,
        details: Mapping[str, Any],
        occurred_at: float,
    ) -> None:
        sequence = int(self.redis.incr(f"{self._audit_key}:sequence"))
        event = _audit_event(
            action,
            resource_id,
            actor,
            details,
            occurred_at=occurred_at,
            sequence=sequence,
        )
        with self.redis.pipeline() as pipe:
            pipe.lpush(self._audit_key, _canonical_json(event, "audit event"))
            pipe.ltrim(self._audit_key, 0, MAX_AUDIT_EVENTS - 1)
            pipe.execute()


def build_lifecycle_operations(
    *,
    redis_url: str | None = None,
    allow_memory_fallback: bool = True,
    **kwargs: Any,
) -> InMemoryLifecycleOperations | RedisLifecycleOperations:
    """Use Redis when configured; never hide a configured Redis failure."""
    configured_url = redis_url if redis_url is not None else os.getenv("REDIS_URL")
    if configured_url:
        from redis import Redis

        client = Redis.from_url(configured_url, decode_responses=True)
        client.ping()
        return RedisLifecycleOperations(client, **kwargs)
    if not allow_memory_fallback:
        raise RuntimeError("REDIS_URL is required for durable lifecycle operations")
    return InMemoryLifecycleOperations()


def operations_manifest() -> dict[str, Any]:
    return {
        "version": VERSION,
        "name": "Model Lifecycle and Governance",
        "registry_contract_version": REGISTRY_CONTRACT_VERSION,
        "features": {
            "persistent_registry_adapters": True,
            "redis_registry": True,
            "atomic_lifecycle_transitions": True,
            "append_only_audit_history": True,
            "four_eyes_approvals": True,
            "evidence_bound_approvals": True,
            "lifecycle_health": True,
            "deterministic_alerts": True,
            "operator_workspace": True,
            "automatic_training": False,
            "automatic_traffic_mutation": False,
            "automatic_deployment": False,
        },
        "controlled_transitions": sorted(CONTROLLED_TRANSITIONS),
        "approval_actions": sorted(_APPROVAL_ACTIONS),
        "storage_backends": ["redis", "memory"],
        "limits": {
            "metadata_bytes": MAX_METADATA_BYTES,
            "audit_events": MAX_AUDIT_EVENTS,
            "alerts": MAX_ALERTS,
            "approvals": MAX_APPROVALS,
        },
        "next_increment": None,
    }


def workspace_manifest() -> dict[str, Any]:
    return {
        "version": VERSION,
        "name": "Model Operations Workspace",
        "route": "/model-operations",
        "panels": [
            {"id": "registry", "label": "Registry"},
            {"id": "approvals", "label": "Approvals"},
            {"id": "health", "label": "Health"},
            {"id": "audit", "label": "Audit"},
        ],
        "guardrails": [
            "Controlled transitions require a distinct approver.",
            "Approvals bind to one resource, action, evidence digest, and expiry.",
            "Health and alert results use only caller-supplied observations.",
            "No workspace action trains, deploys, or shifts serving traffic automatically.",
        ],
    }
