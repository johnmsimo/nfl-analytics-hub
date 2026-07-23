"""Redis Streams transport and in-memory fallback for v4.2 jobs."""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from distributed_v42 import transition_job

VERSION = "4.2.1"
JOB_CONTRACT_VERSION = "4.2.0"
DEFAULT_STREAM = "jobs"
DEFAULT_GROUP = "workers"
DEFAULT_LEASE_SECONDS = 60
MAX_BATCH_SIZE = 100
MAX_LEASE_SECONDS = 3_600

_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


def _timestamp(value: Any, field: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a finite non-negative number") from exc
    if result < 0 or result == float("inf") or result != result:
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
    result = str(value or "").strip()
    if not _IDENTIFIER.fullmatch(result):
        raise ValueError(f"{field} has an unsupported format")
    return result


def _job_copy(job: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(job, Mapping):
        raise ValueError("job must be a JSON object")
    required = ("job_id", "payload_digest", "status", "attempt", "max_attempts")
    if any(key not in job for key in required):
        raise ValueError("job does not satisfy the v4.2.0 contract")
    if str(job.get("version")) != JOB_CONTRACT_VERSION:
        raise ValueError("job must use the v4.2.0 contract")
    return deepcopy(dict(job))


def _job_json(job: Mapping[str, Any]) -> str:
    try:
        return json.dumps(
            _job_copy(job),
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("job must be JSON-safe and contain only finite numbers") from exc


def _latency_summary(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"samples": 0, "average": None, "p95": None, "maximum": None}
    ordered = sorted(values)
    p95_index = max(0, min(len(ordered) - 1, (95 * len(ordered) + 99) // 100 - 1))
    return {
        "samples": len(ordered),
        "average": round(sum(ordered) / len(ordered), 6),
        "p95": round(ordered[p95_index], 6),
        "maximum": round(ordered[-1], 6),
    }


def _dead_letter_record(
    job: Mapping[str, Any],
    message_id: str,
    *,
    recorded_at: float,
) -> dict[str, Any]:
    current = _job_copy(job)
    if current["status"] != "failed":
        raise ValueError("dead-letter records require a failed job")
    timestamp = _timestamp(recorded_at, "recorded_at")
    identity = ":".join(
        (
            str(current["job_id"]),
            str(current["attempt"]),
            str(current.get("completed_at")),
            str(message_id),
        )
    )
    return {
        "version": "4.2.3",
        "dead_letter_id": ("dead_" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]),
        "message_id": str(message_id),
        "job_id": str(current["job_id"]),
        "job_type": str(current.get("job_type", "")),
        "namespace": str(current.get("namespace", "")),
        "attempt": int(current["attempt"]),
        "max_attempts": int(current["max_attempts"]),
        "payload_digest": str(current.get("payload_digest", "")),
        "submitted_at": current.get("submitted_at"),
        "started_at": current.get("started_at"),
        "failed_at": current.get("completed_at"),
        "recorded_at": timestamp,
        "worker_id": current.get("worker_id"),
        "error": str(current.get("error", ""))[:2_000],
    }


def normalize_lease(
    job: Mapping[str, Any],
    message_id: str,
    worker_id: str,
    *,
    claimed_at: float | None = None,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
) -> dict[str, Any]:
    """Build a deterministic, inspectable worker lease."""
    normalized_job = _job_copy(job)
    normalized_message = _identifier(message_id, "message_id")
    normalized_worker = _identifier(worker_id, "worker_id")
    if normalized_job["status"] != "running":
        raise ValueError("a lease requires a running job")
    if normalized_job.get("worker_id") != normalized_worker:
        raise ValueError("worker_id must match the running job")
    claimed = _timestamp(time.time() if claimed_at is None else claimed_at, "claimed_at")
    duration = _integer(
        lease_seconds,
        "lease_seconds",
        1,
        MAX_LEASE_SECONDS,
    )
    identity = ":".join(
        (
            normalized_message,
            str(normalized_job["job_id"]),
            normalized_worker,
            str(normalized_job["attempt"]),
            f"{claimed:.6f}",
        )
    )
    return {
        "version": VERSION,
        "lease_token": "lease_" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24],
        "message_id": normalized_message,
        "job_id": str(normalized_job["job_id"]),
        "worker_id": normalized_worker,
        "attempt": int(normalized_job["attempt"]),
        "claimed_at": claimed,
        "expires_at": round(claimed + duration, 6),
        "lease_seconds": duration,
    }


def recover_stale_job(
    job: Mapping[str, Any],
    lease: Mapping[str, Any],
    worker_id: str,
    *,
    recovered_at: float | None = None,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
) -> dict[str, Any]:
    """Recover one expired lease without bypassing v4.2.0 attempt limits."""
    current = _job_copy(job)
    if not isinstance(lease, Mapping):
        raise ValueError("lease must be a JSON object")
    now = _timestamp(time.time() if recovered_at is None else recovered_at, "recovered_at")
    expires_at = _timestamp(lease.get("expires_at"), "expires_at")
    if current["job_id"] != lease.get("job_id"):
        raise ValueError("lease job_id does not match the job")
    if now < expires_at:
        return {"action": "active", "job": current, "lease": deepcopy(dict(lease))}
    if current["status"] != "running":
        raise ValueError("only a running job lease can be recovered")

    failed = transition_job(
        current,
        "failed",
        now=now,
        error="worker lease expired",
    )
    if failed["attempt"] >= failed["max_attempts"]:
        return {"action": "exhausted", "job": failed, "lease": None}

    queued = transition_job(failed, "queued", now=now)
    running = transition_job(
        queued,
        "running",
        now=now,
        worker_id=_identifier(worker_id, "worker_id"),
    )
    replacement = normalize_lease(
        running,
        str(lease.get("message_id", "")),
        str(running["worker_id"]),
        claimed_at=now,
        lease_seconds=lease_seconds,
    )
    return {"action": "reclaimed", "job": running, "lease": replacement}


class InMemoryStreamTransport:
    """Thread-safe development transport with Redis-compatible semantics."""

    backend = "memory"

    def __init__(self, lease_seconds: int = DEFAULT_LEASE_SECONDS) -> None:
        self.lease_seconds = _integer(
            lease_seconds,
            "lease_seconds",
            1,
            MAX_LEASE_SECONDS,
        )
        self._jobs: dict[str, dict[str, Any]] = {}
        self._messages: list[dict[str, str]] = []
        self._pending: dict[str, dict[str, Any]] = {}
        self._acknowledged: set[str] = set()
        self._dead_letters: dict[str, dict[str, Any]] = {}
        self._claim_latencies: list[float] = []
        self._completion_latencies: list[float] = []
        self._sequence = 0
        self._lock = threading.RLock()

    def ensure_group(self) -> bool:
        return True

    def enqueue(self, job: Mapping[str, Any]) -> dict[str, Any]:
        candidate = _job_copy(job)
        if candidate["status"] != "queued":
            raise ValueError("only queued jobs can be enqueued")
        job_id = str(candidate["job_id"])
        with self._lock:
            existing = self._jobs.get(job_id)
            if existing is not None:
                if existing["payload_digest"] != candidate["payload_digest"]:
                    raise ValueError("job_id conflicts with an existing payload")
                message_id = next(item["message_id"] for item in self._messages if item["job_id"] == job_id)
                return {
                    "accepted": False,
                    "deduplicated": True,
                    "message_id": message_id,
                    "job": deepcopy(existing),
                }
            self._sequence += 1
            message_id = f"{self._sequence}-0"
            self._jobs[job_id] = candidate
            self._messages.append({"message_id": message_id, "job_id": job_id})
            return {
                "accepted": True,
                "deduplicated": False,
                "message_id": message_id,
                "job": deepcopy(candidate),
            }

    def claim(
        self,
        worker_id: str,
        *,
        count: int = 1,
        now: float | None = None,
        block_ms: int = 0,
    ) -> list[dict[str, Any]]:
        del block_ms
        worker = _identifier(worker_id, "worker_id")
        limit = _integer(count, "count", 1, MAX_BATCH_SIZE)
        claimed_at = _timestamp(time.time() if now is None else now, "claimed_at")
        claimed: list[dict[str, Any]] = []
        with self._lock:
            for message in self._messages:
                message_id = message["message_id"]
                if message_id in self._pending or message_id in self._acknowledged:
                    continue
                job = transition_job(
                    self._jobs[message["job_id"]],
                    "running",
                    now=claimed_at,
                    worker_id=worker,
                )
                lease = normalize_lease(
                    job,
                    message_id,
                    worker,
                    claimed_at=claimed_at,
                    lease_seconds=self.lease_seconds,
                )
                self._jobs[message["job_id"]] = job
                self._pending[message_id] = lease
                self._claim_latencies.append(max(0.0, claimed_at - float(job["submitted_at"])))
                self._claim_latencies = self._claim_latencies[-1_000:]
                claimed.append(
                    {
                        "message_id": message_id,
                        "job": deepcopy(job),
                        "lease": deepcopy(lease),
                    }
                )
                if len(claimed) >= limit:
                    break
        return claimed

    def acknowledge(
        self,
        message_id: str,
        worker_id: str,
        job: Mapping[str, Any],
    ) -> dict[str, Any]:
        message = _identifier(message_id, "message_id")
        worker = _identifier(worker_id, "worker_id")
        completed = _job_copy(job)
        if completed["status"] not in {"succeeded", "failed", "cancelled"}:
            raise ValueError("acknowledgement requires a terminal job")
        with self._lock:
            lease = self._pending.get(message)
            if lease is None:
                raise KeyError("message is not pending")
            if lease["worker_id"] != worker:
                raise ValueError("worker does not own the lease")
            if lease["job_id"] != completed["job_id"]:
                raise ValueError("job_id does not match the lease")
            self._jobs[str(completed["job_id"])] = completed
            completed_at = float(completed.get("completed_at") or time.time())
            self._completion_latencies.append(max(0.0, completed_at - float(completed["submitted_at"])))
            self._completion_latencies = self._completion_latencies[-1_000:]
            if completed["status"] == "failed":
                record = _dead_letter_record(
                    completed,
                    message,
                    recorded_at=completed_at,
                )
                self._dead_letters[record["dead_letter_id"]] = record
            self._pending.pop(message, None)
            self._acknowledged.add(message)
            return {
                "acknowledged": True,
                "message_id": message,
                "job": deepcopy(completed),
            }

    def recover_stale(
        self,
        worker_id: str,
        *,
        now: float | None = None,
        count: int = MAX_BATCH_SIZE,
    ) -> list[dict[str, Any]]:
        worker = _identifier(worker_id, "worker_id")
        recovered_at = _timestamp(time.time() if now is None else now, "recovered_at")
        limit = _integer(count, "count", 1, MAX_BATCH_SIZE)
        recovered: list[dict[str, Any]] = []
        with self._lock:
            stale = [
                (message_id, lease)
                for message_id, lease in self._pending.items()
                if lease["expires_at"] <= recovered_at
            ][:limit]
            for message_id, lease in stale:
                outcome = recover_stale_job(
                    self._jobs[str(lease["job_id"])],
                    lease,
                    worker,
                    recovered_at=recovered_at,
                    lease_seconds=self.lease_seconds,
                )
                self._jobs[str(lease["job_id"])] = outcome["job"]
                if outcome["action"] == "reclaimed":
                    self._pending[message_id] = outcome["lease"]
                else:
                    record = _dead_letter_record(
                        outcome["job"],
                        message_id,
                        recorded_at=recovered_at,
                    )
                    self._dead_letters[record["dead_letter_id"]] = record
                    self._pending.pop(message_id, None)
                    self._acknowledged.add(message_id)
                recovered.append({"message_id": message_id, **deepcopy(outcome)})
        return recovered

    def operations_snapshot(self, *, now: float | None = None) -> dict[str, Any]:
        measured_at = _timestamp(time.time() if now is None else now, "now")
        with self._lock:
            queued_jobs = [
                self._jobs[message["job_id"]]
                for message in self._messages
                if message["message_id"] not in self._pending
                and message["message_id"] not in self._acknowledged
            ]
            oldest_age = (
                max(0.0, measured_at - min(float(job["submitted_at"]) for job in queued_jobs))
                if queued_jobs
                else 0.0
            )
            return {
                "version": "4.2.3",
                "backend": self.backend,
                "measured_at": measured_at,
                "queue_depth": len(queued_jobs),
                "pending_depth": len(self._pending),
                "acknowledged_total": len(self._acknowledged),
                "dead_letter_depth": len(self._dead_letters),
                "oldest_queued_age_seconds": round(oldest_age, 6),
                "claim_latency_seconds": _latency_summary(self._claim_latencies),
                "completion_latency_seconds": _latency_summary(self._completion_latencies),
            }

    def list_dead_letters(self, *, limit: int = 50) -> list[dict[str, Any]]:
        bounded = _integer(limit, "limit", 1, 100)
        with self._lock:
            records = sorted(
                self._dead_letters.values(),
                key=lambda item: (float(item["recorded_at"]), item["dead_letter_id"]),
                reverse=True,
            )
            return deepcopy(records[:bounded])

    def health(self) -> dict[str, Any]:
        return {
            "component": "job_transport",
            "backend": self.backend,
            "healthy": True,
            "durable": False,
        }


class RedisStreamTransport:
    """Redis Streams consumer-group transport using the v4.2.0 job envelope."""

    backend = "redis"

    _ENQUEUE_SCRIPT = """
local existing = redis.call('HGET', KEYS[1], ARGV[1])
if existing then
  if existing == ARGV[2] then
    return {0, redis.call('HGET', KEYS[2], ARGV[1]) or ''}
  end
  return {-1, ''}
end
redis.call('HSET', KEYS[1], ARGV[1], ARGV[2])
redis.call('HSET', KEYS[3], ARGV[1], ARGV[3])
local message_id = redis.call('XADD', KEYS[4], '*', 'job_id', ARGV[1])
redis.call('HSET', KEYS[2], ARGV[1], message_id)
return {1, message_id}
"""

    def __init__(
        self,
        client: Any,
        *,
        key_prefix: str = "nfl:v42",
        stream: str = DEFAULT_STREAM,
        group: str = DEFAULT_GROUP,
        lease_seconds: int = DEFAULT_LEASE_SECONDS,
    ) -> None:
        self.client = client
        self.key_prefix = _identifier(key_prefix, "key_prefix")
        self.group = _identifier(group, "group")
        self.stream_key = f"{self.key_prefix}:{_identifier(stream, 'stream')}"
        self.jobs_key = f"{self.key_prefix}:job-state"
        self.digests_key = f"{self.key_prefix}:job-digests"
        self.messages_key = f"{self.key_prefix}:job-messages"
        self.leases_key = f"{self.key_prefix}:leases"
        self.dead_letters_key = f"{self.key_prefix}:dead-letters"
        self.claim_latencies_key = f"{self.key_prefix}:metrics:claim-latency"
        self.completion_latencies_key = f"{self.key_prefix}:metrics:completion-latency"
        self.operations_metrics_key = f"{self.key_prefix}:metrics:operations"
        self.lease_seconds = _integer(
            lease_seconds,
            "lease_seconds",
            1,
            MAX_LEASE_SECONDS,
        )

    @staticmethod
    def _text(value: Any) -> str:
        return value.decode("utf-8") if isinstance(value, bytes) else str(value)

    def ensure_group(self) -> bool:
        try:
            self.client.xgroup_create(
                self.stream_key,
                self.group,
                id="0",
                mkstream=True,
            )
            return True
        except Exception as exc:
            if "BUSYGROUP" not in str(exc):
                raise
            return False

    def enqueue(self, job: Mapping[str, Any]) -> dict[str, Any]:
        candidate = _job_copy(job)
        if candidate["status"] != "queued":
            raise ValueError("only queued jobs can be enqueued")
        self.ensure_group()
        job_id = str(candidate["job_id"])
        raw = _job_json(candidate)
        response = self.client.eval(
            self._ENQUEUE_SCRIPT,
            4,
            self.digests_key,
            self.messages_key,
            self.jobs_key,
            self.stream_key,
            job_id,
            str(candidate["payload_digest"]),
            raw,
        )
        state = int(response[0])
        if state == -1:
            raise ValueError("job_id conflicts with an existing payload")
        return {
            "accepted": state == 1,
            "deduplicated": state == 0,
            "message_id": self._text(response[1]),
            "job": candidate,
        }

    def _load_job(self, job_id: str) -> dict[str, Any]:
        raw = self.client.hget(self.jobs_key, job_id)
        if raw is None:
            raise KeyError("job state not found")
        return json.loads(self._text(raw))

    def claim(
        self,
        worker_id: str,
        *,
        count: int = 1,
        now: float | None = None,
        block_ms: int = 0,
    ) -> list[dict[str, Any]]:
        worker = _identifier(worker_id, "worker_id")
        limit = _integer(count, "count", 1, MAX_BATCH_SIZE)
        block = _integer(block_ms, "block_ms", 0, 60_000)
        claimed_at = _timestamp(time.time() if now is None else now, "claimed_at")
        self.ensure_group()
        batches = self.client.xreadgroup(
            self.group,
            worker,
            {self.stream_key: ">"},
            count=limit,
            block=block or None,
        )
        claimed: list[dict[str, Any]] = []
        for _, messages in batches:
            for raw_message_id, fields in messages:
                message_id = self._text(raw_message_id)
                decoded = {self._text(key): self._text(value) for key, value in fields.items()}
                job = transition_job(
                    self._load_job(decoded["job_id"]),
                    "running",
                    now=claimed_at,
                    worker_id=worker,
                )
                lease = normalize_lease(
                    job,
                    message_id,
                    worker,
                    claimed_at=claimed_at,
                    lease_seconds=self.lease_seconds,
                )
                with self.client.pipeline(transaction=True) as pipe:
                    pipe.hset(self.jobs_key, job["job_id"], _job_json(job))
                    pipe.hset(
                        self.leases_key,
                        message_id,
                        json.dumps(lease, separators=(",", ":"), sort_keys=True),
                    )
                    pipe.lpush(
                        self.claim_latencies_key,
                        max(0.0, claimed_at - float(job["submitted_at"])),
                    )
                    pipe.ltrim(self.claim_latencies_key, 0, 999)
                    pipe.execute()
                claimed.append({"message_id": message_id, "job": job, "lease": lease})
        return claimed

    def acknowledge(
        self,
        message_id: str,
        worker_id: str,
        job: Mapping[str, Any],
    ) -> dict[str, Any]:
        message = _identifier(message_id, "message_id")
        worker = _identifier(worker_id, "worker_id")
        completed = _job_copy(job)
        if completed["status"] not in {"succeeded", "failed", "cancelled"}:
            raise ValueError("acknowledgement requires a terminal job")
        raw_lease = self.client.hget(self.leases_key, message)
        if raw_lease is None:
            raise KeyError("message is not pending")
        lease = json.loads(self._text(raw_lease))
        if lease["worker_id"] != worker:
            raise ValueError("worker does not own the lease")
        if lease["job_id"] != completed["job_id"]:
            raise ValueError("job_id does not match the lease")
        completed_at = float(completed.get("completed_at") or time.time())
        with self.client.pipeline(transaction=True) as pipe:
            pipe.hset(self.jobs_key, completed["job_id"], _job_json(completed))
            pipe.hdel(self.leases_key, message)
            pipe.lpush(
                self.completion_latencies_key,
                max(0.0, completed_at - float(completed["submitted_at"])),
            )
            pipe.ltrim(self.completion_latencies_key, 0, 999)
            if completed["status"] == "failed":
                record = _dead_letter_record(
                    completed,
                    message,
                    recorded_at=completed_at,
                )
                pipe.hset(
                    self.dead_letters_key,
                    record["dead_letter_id"],
                    json.dumps(record, separators=(",", ":"), sort_keys=True),
                )
            pipe.hincrby(self.operations_metrics_key, "acknowledged", 1)
            pipe.xack(self.stream_key, self.group, message)
            results = pipe.execute()
        return {
            "acknowledged": bool(results[-1]),
            "message_id": message,
            "job": completed,
        }

    def recover_stale(
        self,
        worker_id: str,
        *,
        now: float | None = None,
        count: int = MAX_BATCH_SIZE,
        min_idle_ms: int | None = None,
    ) -> list[dict[str, Any]]:
        worker = _identifier(worker_id, "worker_id")
        recovered_at = _timestamp(time.time() if now is None else now, "recovered_at")
        limit = _integer(count, "count", 1, MAX_BATCH_SIZE)
        minimum_idle = (
            self.lease_seconds * 1_000
            if min_idle_ms is None
            else _integer(min_idle_ms, "min_idle_ms", 0, MAX_LEASE_SECONDS * 1_000)
        )
        self.ensure_group()
        response = self.client.xautoclaim(
            self.stream_key,
            self.group,
            worker,
            min_idle_time=minimum_idle,
            start_id="0-0",
            count=limit,
        )
        messages = response[1] if len(response) > 1 else []
        recovered: list[dict[str, Any]] = []
        for raw_message_id, fields in messages:
            message_id = self._text(raw_message_id)
            decoded = {self._text(key): self._text(value) for key, value in fields.items()}
            raw_lease = self.client.hget(self.leases_key, message_id)
            current = self._load_job(decoded["job_id"])
            if raw_lease is None and current["status"] == "queued":
                running = transition_job(
                    current,
                    "running",
                    now=recovered_at,
                    worker_id=worker,
                )
                replacement = normalize_lease(
                    running,
                    message_id,
                    worker,
                    claimed_at=recovered_at,
                    lease_seconds=self.lease_seconds,
                )
                outcome = {
                    "action": "reclaimed",
                    "job": running,
                    "lease": replacement,
                }
            else:
                lease = (
                    json.loads(self._text(raw_lease))
                    if raw_lease is not None
                    else {
                        "message_id": message_id,
                        "job_id": decoded["job_id"],
                        "expires_at": recovered_at,
                    }
                )
                effective_recovered_at = max(
                    recovered_at,
                    float(lease["expires_at"]),
                )
                outcome = recover_stale_job(
                    current,
                    lease,
                    worker,
                    recovered_at=effective_recovered_at,
                    lease_seconds=self.lease_seconds,
                )
            with self.client.pipeline(transaction=True) as pipe:
                pipe.hset(
                    self.jobs_key,
                    outcome["job"]["job_id"],
                    _job_json(outcome["job"]),
                )
                if outcome["action"] == "reclaimed":
                    pipe.hset(
                        self.leases_key,
                        message_id,
                        json.dumps(outcome["lease"], separators=(",", ":"), sort_keys=True),
                    )
                else:
                    record = _dead_letter_record(
                        outcome["job"],
                        message_id,
                        recorded_at=recovered_at,
                    )
                    pipe.hset(
                        self.dead_letters_key,
                        record["dead_letter_id"],
                        json.dumps(record, separators=(",", ":"), sort_keys=True),
                    )
                    pipe.hdel(self.leases_key, message_id)
                    pipe.hincrby(self.operations_metrics_key, "acknowledged", 1)
                    pipe.xack(self.stream_key, self.group, message_id)
                pipe.execute()
            recovered.append({"message_id": message_id, **outcome})
        return recovered

    def operations_snapshot(self, *, now: float | None = None) -> dict[str, Any]:
        measured_at = _timestamp(time.time() if now is None else now, "now")
        self.ensure_group()
        group_info = {}
        for candidate in self.client.xinfo_groups(self.stream_key):
            normalized = {self._text(key): value for key, value in candidate.items()}
            if self._text(normalized.get("name", "")) == self.group:
                group_info = normalized
                break
        pending_summary = self.client.xpending(self.stream_key, self.group)
        if isinstance(pending_summary, Mapping):
            pending = int(pending_summary.get("pending") or pending_summary.get(b"pending") or 0)
        else:
            pending = int(pending_summary[0]) if pending_summary else 0
        total = int(self.client.xlen(self.stream_key))
        acknowledged = int(self.client.hget(self.operations_metrics_key, "acknowledged") or 0)
        entries_read = group_info.get("entries-read")
        if entries_read is not None:
            acknowledged = max(acknowledged, int(entries_read) - pending)
        queue_depth = max(0, total - acknowledged - pending)
        claim_values = [
            float(self._text(value)) for value in self.client.lrange(self.claim_latencies_key, 0, 999)
        ]
        completion_values = [
            float(self._text(value))
            for value in self.client.lrange(
                self.completion_latencies_key,
                0,
                999,
            )
        ]
        return {
            "version": "4.2.3",
            "backend": self.backend,
            "measured_at": measured_at,
            "queue_depth": queue_depth,
            "pending_depth": pending,
            "acknowledged_total": acknowledged,
            "dead_letter_depth": int(self.client.hlen(self.dead_letters_key)),
            "oldest_queued_age_seconds": None,
            "claim_latency_seconds": _latency_summary(claim_values),
            "completion_latency_seconds": _latency_summary(completion_values),
        }

    def list_dead_letters(self, *, limit: int = 50) -> list[dict[str, Any]]:
        bounded = _integer(limit, "limit", 1, 100)
        records: list[dict[str, Any]] = []
        cursor: int | str = 0
        while True:
            cursor, batch = self.client.hscan(
                self.dead_letters_key,
                cursor=cursor,
                count=bounded,
            )
            records.extend(json.loads(self._text(raw_record)) for raw_record in batch.values())
            if len(records) >= bounded or int(cursor) == 0:
                break
        records.sort(
            key=lambda item: (float(item["recorded_at"]), item["dead_letter_id"]),
            reverse=True,
        )
        return records[:bounded]

    def health(self) -> dict[str, Any]:
        return {
            "component": "job_transport",
            "backend": self.backend,
            "healthy": bool(self.client.ping()),
            "durable": True,
        }


def build_transport(
    redis_url: str | None = None,
    *,
    client: Any = None,
    allow_memory_fallback: bool = True,
    **kwargs: Any,
) -> InMemoryStreamTransport | RedisStreamTransport:
    """Build Redis transport when configured, otherwise the development fallback."""
    configured_url = redis_url if redis_url is not None else os.getenv("REDIS_URL")
    if client is not None:
        return RedisStreamTransport(client, **kwargs)
    if configured_url:
        from redis import Redis

        redis_client = Redis.from_url(configured_url, decode_responses=True)
        redis_client.ping()
        return RedisStreamTransport(redis_client, **kwargs)
    if not allow_memory_fallback:
        raise RuntimeError("REDIS_URL is required when memory fallback is disabled")
    memory_kwargs = {key: value for key, value in kwargs.items() if key == "lease_seconds"}
    return InMemoryStreamTransport(**memory_kwargs)


def transport_manifest() -> dict[str, Any]:
    return {
        "version": VERSION,
        "job_contract_version": JOB_CONTRACT_VERSION,
        "name": "Redis Streams Transport",
        "features": {
            "redis_stream_transport": True,
            "consumer_groups": True,
            "external_worker_leases": True,
            "acknowledgements": True,
            "stale_lease_recovery": True,
            "in_memory_transport": True,
        },
        "backends": ["redis", "memory"],
        "limits": {
            "default_lease_seconds": DEFAULT_LEASE_SECONDS,
            "max_lease_seconds": MAX_LEASE_SECONDS,
            "max_batch_size": MAX_BATCH_SIZE,
        },
        "next_increment": "v4.2.2 Distributed execution and typed workers",
    }
