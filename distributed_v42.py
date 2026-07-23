"""Dependency-light v4.2 distributed job contracts."""
from __future__ import annotations

import hashlib
import json
import math
import re
import threading
import time
from collections.abc import Mapping
from copy import deepcopy
from typing import Any

VERSION = "4.2.0"
MAX_PAYLOAD_BYTES = 256 * 1024
MAX_RESULT_BYTES = 256 * 1024
MAX_IDEMPOTENCY_KEY_LENGTH = 128
MAX_ERROR_LENGTH = 2_000

_JOB_TYPE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_STATUSES = {"queued", "running", "succeeded", "failed", "cancelled"}
_TRANSITIONS = {
    "queued": {"running", "cancelled"},
    "running": {"succeeded", "failed", "cancelled"},
    "failed": {"queued"},
    "succeeded": set(),
    "cancelled": set(),
}


def _timestamp(value: Any, field: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a finite number") from exc
    if not math.isfinite(result) or result < 0:
        raise ValueError(f"{field} must be a finite non-negative number")
    return round(result, 6)


def _bounded_integer(value: Any, field: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be an integer")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be an integer") from exc
    if result < minimum or result > maximum:
        raise ValueError(f"{field} must be between {minimum} and {maximum}")
    return result


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


def _digest(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def normalize_job(payload: Mapping[str, Any], now: float | None = None) -> dict[str, Any]:
    """Normalize a caller-supplied job into the stable v4.2 contract."""
    if not isinstance(payload, Mapping):
        raise ValueError("job must be a JSON object")

    job_type = str(payload.get("job_type", "")).strip().lower()
    if not _JOB_TYPE.fullmatch(job_type):
        raise ValueError("job_type must use 1-64 lowercase letters, numbers, dots, dashes, or underscores")

    job_payload = payload.get("payload", {})
    if not isinstance(job_payload, Mapping):
        raise ValueError("payload must be a JSON object")
    canonical_payload = _canonical_json(job_payload, "payload", MAX_PAYLOAD_BYTES)
    payload_digest = _digest(canonical_payload)

    supplied_key = payload.get("idempotency_key")
    if supplied_key is None:
        idempotency_key = _digest(f"{job_type}:{canonical_payload}")[:32]
        key_source = "content"
    else:
        idempotency_key = str(supplied_key).strip()
        if not idempotency_key:
            raise ValueError("idempotency_key cannot be empty")
        if len(idempotency_key) > MAX_IDEMPOTENCY_KEY_LENGTH:
            raise ValueError(
                f"idempotency_key cannot exceed {MAX_IDEMPOTENCY_KEY_LENGTH} characters"
            )
        key_source = "caller"

    namespace = str(payload.get("namespace", "default")).strip().lower()[:64] or "default"
    if not _JOB_TYPE.fullmatch(namespace):
        raise ValueError("namespace must use lowercase letters, numbers, dots, dashes, or underscores")

    submitted_at = _timestamp(time.time() if now is None else now, "submitted_at")
    priority = _bounded_integer(payload.get("priority", 5), "priority", 0, 9)
    max_attempts = _bounded_integer(payload.get("max_attempts", 3), "max_attempts", 1, 10)
    identity = _digest(f"{namespace}:{job_type}:{idempotency_key}")[:24]

    return {
        "version": VERSION,
        "job_id": f"job_{identity}",
        "job_type": job_type,
        "namespace": namespace,
        "status": "queued",
        "priority": priority,
        "attempt": 0,
        "max_attempts": max_attempts,
        "idempotency_key": idempotency_key,
        "idempotency_key_source": key_source,
        "payload_digest": payload_digest,
        "payload": deepcopy(dict(job_payload)),
        "submitted_at": submitted_at,
        "started_at": None,
        "completed_at": None,
        "updated_at": submitted_at,
        "worker_id": None,
        "result": None,
        "error": None,
    }


def transition_job(
    job: Mapping[str, Any],
    target_status: str,
    *,
    now: float | None = None,
    worker_id: str | None = None,
    result: Any = None,
    error: str | None = None,
) -> dict[str, Any]:
    """Validate and apply one job lifecycle transition."""
    if not isinstance(job, Mapping):
        raise ValueError("job must be a JSON object")
    current = str(job.get("status", "")).strip().lower()
    target = str(target_status).strip().lower()
    if current not in _STATUSES:
        raise ValueError("job has an unsupported status")
    if target not in _STATUSES:
        raise ValueError("target_status is unsupported")
    if target not in _TRANSITIONS[current]:
        raise ValueError(f"cannot transition job from {current} to {target}")

    updated = deepcopy(dict(job))
    changed_at = _timestamp(time.time() if now is None else now, "updated_at")
    attempt = _bounded_integer(updated.get("attempt", 0), "attempt", 0, 10)
    max_attempts = _bounded_integer(updated.get("max_attempts", 3), "max_attempts", 1, 10)

    if current == "failed" and target == "queued" and attempt >= max_attempts:
        raise ValueError("job has exhausted its retry attempts")

    normalized_worker = str(worker_id or updated.get("worker_id") or "").strip()[:80]
    if target == "running":
        if not normalized_worker:
            raise ValueError("worker_id is required when a job starts running")
        attempt += 1
        if attempt > max_attempts:
            raise ValueError("job has exhausted its retry attempts")
        updated["started_at"] = changed_at
        updated["completed_at"] = None
    elif target == "succeeded":
        canonical_result = _canonical_json(
            {} if result is None else result,
            "result",
            MAX_RESULT_BYTES,
        )
        updated["result"] = json.loads(canonical_result)
        updated["error"] = None
        updated["completed_at"] = changed_at
    elif target == "failed":
        normalized_error = str(error or "").strip()
        if not normalized_error:
            raise ValueError("error is required when a job fails")
        updated["error"] = normalized_error[:MAX_ERROR_LENGTH]
        updated["result"] = None
        updated["completed_at"] = changed_at
    elif target == "cancelled":
        updated["error"] = str(error or "cancelled").strip()[:MAX_ERROR_LENGTH]
        updated["result"] = None
        updated["completed_at"] = changed_at
    elif target == "queued":
        updated["worker_id"] = None
        updated["started_at"] = None
        updated["completed_at"] = None
        updated["result"] = None
        updated["error"] = None

    updated["status"] = target
    updated["attempt"] = attempt
    updated["worker_id"] = normalized_worker or None
    updated["updated_at"] = changed_at
    return updated


def job_event(
    job: Mapping[str, Any],
    event_type: str,
    sequence: int,
    *,
    occurred_at: float | None = None,
) -> dict[str, Any]:
    """Build an inspectable provider-neutral event envelope."""
    job_id = str(job.get("job_id", "")).strip()
    if not job_id:
        raise ValueError("job_id is required")
    normalized_type = str(event_type).strip().lower().replace(" ", "_")
    if not _JOB_TYPE.fullmatch(normalized_type):
        raise ValueError("event_type has an unsupported format")
    normalized_sequence = _bounded_integer(sequence, "sequence", 1, 1_000_000_000)
    status = str(job.get("status", "")).strip().lower()
    if status not in _STATUSES:
        raise ValueError("job has an unsupported status")
    event_identity = _digest(f"{job_id}:{normalized_sequence}:{normalized_type}:{status}")[:24]
    return {
        "version": VERSION,
        "event_id": f"evt_{event_identity}",
        "event_type": normalized_type,
        "sequence": normalized_sequence,
        "job_id": job_id,
        "job_type": str(job.get("job_type", "")),
        "status": status,
        "attempt": int(job.get("attempt", 0)),
        "occurred_at": _timestamp(
            time.time() if occurred_at is None else occurred_at,
            "occurred_at",
        ),
        "payload_digest": str(job.get("payload_digest", "")),
    }


class InMemoryJobRegistry:
    """Bounded reference registry for tests and single-process development."""

    def __init__(self, max_jobs: int = 1_000) -> None:
        self.max_jobs = _bounded_integer(max_jobs, "max_jobs", 1, 10_000)
        self._jobs: dict[str, dict[str, Any]] = {}
        self._order: list[str] = []
        self._lock = threading.RLock()

    def submit(self, payload: Mapping[str, Any], now: float | None = None) -> dict[str, Any]:
        candidate = normalize_job(payload, now=now)
        with self._lock:
            existing = self._jobs.get(candidate["job_id"])
            if existing is not None:
                if existing["payload_digest"] != candidate["payload_digest"]:
                    raise ValueError("idempotency_key conflicts with an existing payload")
                return {"accepted": False, "deduplicated": True, "job": deepcopy(existing)}
            while len(self._order) >= self.max_jobs:
                removed = self._order.pop(0)
                self._jobs.pop(removed, None)
            self._jobs[candidate["job_id"]] = candidate
            self._order.append(candidate["job_id"])
            return {"accepted": True, "deduplicated": False, "job": deepcopy(candidate)}

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(str(job_id))
            return deepcopy(job) if job is not None else None

    def transition(self, job_id: str, target_status: str, **kwargs: Any) -> dict[str, Any]:
        with self._lock:
            current = self._jobs.get(str(job_id))
            if current is None:
                raise KeyError("job not found")
            updated = transition_job(current, target_status, **kwargs)
            self._jobs[str(job_id)] = updated
            return deepcopy(updated)


def platform_manifest() -> dict[str, Any]:
    return {
        "version": VERSION,
        "name": "Distributed Intelligence Platform",
        "features": {
            "idempotent_job_contracts": True,
            "validated_lifecycle_transitions": True,
            "bounded_payloads": True,
            "provider_neutral_events": True,
            "redis_stream_transport": False,
            "external_worker_leases": False,
        },
        "limits": {
            "payload_bytes": MAX_PAYLOAD_BYTES,
            "result_bytes": MAX_RESULT_BYTES,
            "max_attempts": 10,
            "priority_range": [0, 9],
        },
        "next_increment": "v4.2.1 Redis Streams transport and worker leases",
    }
