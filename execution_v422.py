"""Typed distributed execution for NFL Analytics Hub v4.2 jobs."""
from __future__ import annotations

import hashlib
import json
import math
import os
import random
import signal
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from distributed_v42 import MAX_RESULT_BYTES, transition_job

VERSION = "4.2.2"
JOB_CONTRACT_VERSION = "4.2.0"
TRANSPORT_VERSION = "4.2.1"
MAX_TIMEOUT_SECONDS = 3_600
MAX_WORKER_BATCH_SIZE = 25
MAX_RESULT_TTL_SECONDS = 30 * 24 * 60 * 60
MAX_RESULT_RECORD_BYTES = MAX_RESULT_BYTES + 8_192

Handler = Callable[[Mapping[str, Any], "ExecutionContext"], Any]
PayloadValidator = Callable[[Mapping[str, Any]], dict[str, Any]]


class ExecutionCancelled(RuntimeError):
    """Raised when a cancellation request is visible to a handler."""


class ExecutionTimedOut(BaseException):
    """Raised when a typed handler exceeds its bounded deadline."""


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


def _number(
    value: Any,
    field: str,
    minimum: float,
    maximum: float,
) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a finite number") from exc
    if not math.isfinite(result) or result < minimum or result > maximum:
        raise ValueError(f"{field} must be between {minimum} and {maximum}")
    return result


def _text(value: Any, field: str, maximum: int = 128) -> str:
    result = str(value or "").strip()
    if not result:
        raise ValueError(f"{field} is required")
    if len(result) > maximum:
        raise ValueError(f"{field} cannot exceed {maximum} characters")
    return result


def _mapping(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be a JSON object")
    return deepcopy(dict(value))


def _mapping_list(
    value: Any,
    field: str,
    *,
    maximum: int = 10_000,
) -> list[dict[str, Any]]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValueError(f"{field} must be a list")
    if len(value) > maximum:
        raise ValueError(f"{field} cannot contain more than {maximum} items")
    if not all(isinstance(item, Mapping) for item in value):
        raise ValueError(f"{field} must contain only JSON objects")
    return [deepcopy(dict(item)) for item in value]


def _string_list(
    value: Any,
    field: str,
    *,
    maximum: int = 25,
) -> list[str]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValueError(f"{field} must be a list")
    if len(value) > maximum:
        raise ValueError(f"{field} cannot contain more than {maximum} items")
    result = []
    for item in value:
        text = _text(item, field, 80)
        if text not in result:
            result.append(text)
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
        raise ValueError(
            f"{field} must be JSON-safe and contain only finite numbers"
        ) from exc
    if len(raw.encode("utf-8")) > maximum_bytes:
        raise ValueError(f"{field} exceeds {maximum_bytes} bytes")
    return raw


def _model_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    result = _mapping(payload, "payload")
    rows = _mapping_list(result.get("rows"), "rows", maximum=1_000)
    if not rows:
        raise ValueError("rows cannot be empty")
    market = _text(result.get("market"), "market", 40).lower()
    allowed = {
        "pass_yds",
        "pass_tds",
        "rush_yds",
        "receptions",
        "rec_yds",
        "anytime_td",
    }
    if market not in allowed:
        raise ValueError("market is unsupported")
    normalized = {
        "rows": rows,
        "market": market,
        "position": _text(result.get("position", "WR"), "position", 10).upper(),
        "opponent": (
            str(result.get("opponent")).strip().upper()[:10]
            if result.get("opponent") is not None
            else None
        ),
        "dvp": _mapping(result.get("dvp", {}), "dvp"),
    }
    if result.get("line") is not None:
        normalized["line"] = _number(
            result["line"],
            "line",
            -10_000,
            10_000,
        )
    return normalized


def _simulation_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    result = _mapping(payload, "payload")
    return {
        "home_win_probability": _number(
            result.get("home_win_probability"),
            "home_win_probability",
            0,
            1,
        ),
        "trials": _integer(result.get("trials", 10_000), "trials", 100, 100_000),
        "seed": _integer(result.get("seed", 42), "seed", 0, 2_147_483_647),
    }


def _scouting_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    result = _mapping(payload, "payload")
    operation = _text(result.get("operation"), "operation", 40).lower()
    if operation == "player_similarity":
        return {
            "operation": operation,
            "target": _mapping(result.get("target"), "target"),
            "candidates": _mapping_list(
                result.get("candidates"),
                "candidates",
                maximum=500,
            ),
            "metrics": (
                _string_list(result["metrics"], "metrics")
                if result.get("metrics") is not None
                else None
            ),
            "limit": _integer(result.get("limit", 5), "limit", 1, 25),
        }
    if operation == "team_style_clusters":
        return {
            "operation": operation,
            "teams": _mapping_list(result.get("teams"), "teams", maximum=100),
            "metrics": (
                _string_list(result["metrics"], "metrics")
                if result.get("metrics") is not None
                else None
            ),
            "cluster_count": _integer(
                result.get("cluster_count", 3),
                "cluster_count",
                1,
                8,
            ),
        }
    if operation == "personnel_tendencies":
        return {
            "operation": operation,
            "plays": _mapping_list(result.get("plays"), "plays", maximum=10_000),
            "min_snaps": _integer(
                result.get("min_snaps", 1),
                "min_snaps",
                1,
                500,
            ),
        }
    raise ValueError("operation is unsupported")


def _backfill_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    result = _mapping(payload, "payload")
    start = _integer(
        result.get("start_season"),
        "start_season",
        1999,
        2100,
    )
    end = _integer(result.get("end_season"), "end_season", start, 2100)
    datasets = _string_list(result.get("datasets"), "datasets", maximum=20)
    if not datasets:
        raise ValueError("datasets cannot be empty")
    return {
        "start_season": start,
        "end_season": end,
        "datasets": datasets,
        "commercial": _string_list(
            result.get("commercial", []),
            "commercial",
            maximum=10,
        ),
        "continue_on_error": bool(result.get("continue_on_error", True)),
    }


def _report_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    result = _mapping(payload, "payload")
    return {"report": _mapping(result.get("report"), "report")}


def _handle_model(
    payload: Mapping[str, Any],
    context: "ExecutionContext",
) -> dict[str, Any]:
    from projections import prob_over, project_stat

    context.checkpoint()
    projection = project_stat(
        list(payload["rows"]),
        str(payload["market"]),
        opponent=payload.get("opponent"),
        dvp=dict(payload.get("dvp", {})),
        position=str(payload["position"]),
    )
    if projection is None:
        raise ValueError("model requires at least three usable game rows")
    result: dict[str, Any] = {"projection": projection}
    if payload.get("line") is not None:
        result["line"] = payload["line"]
        result["probability_over"] = prob_over(projection, float(payload["line"]))
    context.checkpoint()
    return result


def _handle_simulation(
    payload: Mapping[str, Any],
    context: "ExecutionContext",
) -> dict[str, Any]:
    probability = float(payload["home_win_probability"])
    trials = int(payload["trials"])
    seed = int(payload["seed"])
    generator = random.Random(seed)
    home_wins = 0
    for index in range(trials):
        if generator.random() < probability:
            home_wins += 1
        if index % 1_000 == 0:
            context.checkpoint()
    return {
        "method": "seeded_bernoulli",
        "seed": seed,
        "trials": trials,
        "home_wins": home_wins,
        "away_wins": trials - home_wins,
        "estimated_home_win_probability": round(home_wins / trials, 6),
    }


def _handle_scouting(
    payload: Mapping[str, Any],
    context: "ExecutionContext",
) -> dict[str, Any]:
    from scouting_v41 import (
        cluster_team_styles,
        personnel_tendencies,
        player_similarity,
    )

    context.checkpoint()
    operation = payload["operation"]
    if operation == "player_similarity":
        result = player_similarity(
            payload["target"],
            payload["candidates"],
            metrics=payload.get("metrics"),
            limit=int(payload["limit"]),
        )
    elif operation == "team_style_clusters":
        result = cluster_team_styles(
            payload["teams"],
            metrics=payload.get("metrics"),
            cluster_count=int(payload["cluster_count"]),
        )
    else:
        result = personnel_tendencies(
            payload["plays"],
            min_snaps=int(payload["min_snaps"]),
        )
    context.checkpoint()
    return result


def _handle_backfill(
    payload: Mapping[str, Any],
    context: "ExecutionContext",
) -> dict[str, Any]:
    from historical_backfill import run

    context.checkpoint()
    result = run(
        int(payload["start_season"]),
        int(payload["end_season"]),
        list(payload["datasets"]),
        list(payload["commercial"]),
        continue_on_error=bool(payload["continue_on_error"]),
    )
    context.checkpoint()
    return {"completed": True, "summary": result}


def _handle_report(
    payload: Mapping[str, Any],
    context: "ExecutionContext",
) -> dict[str, Any]:
    from workspace_v413 import normalize_workspace_report

    context.checkpoint()
    result = normalize_workspace_report(payload["report"])
    context.checkpoint()
    return result


@dataclass(frozen=True)
class HandlerSpec:
    job_type: str
    family: str
    timeout_seconds: int
    validator: PayloadValidator
    handler: Handler


_DEFAULT_SPECS = (
    HandlerSpec("model.project", "model", 120, _model_payload, _handle_model),
    HandlerSpec(
        "simulation.run",
        "simulation",
        300,
        _simulation_payload,
        _handle_simulation,
    ),
    HandlerSpec(
        "scouting.analyze",
        "scouting",
        180,
        _scouting_payload,
        _handle_scouting,
    ),
    HandlerSpec("backfill.run", "backfill", 3_600, _backfill_payload, _handle_backfill),
    HandlerSpec("report.generate", "report", 120, _report_payload, _handle_report),
)


class TypedHandlerRegistry:
    """Static allowlist of typed job handlers; no dynamic imports are accepted."""

    def __init__(self, specs: Sequence[HandlerSpec] = _DEFAULT_SPECS) -> None:
        self._specs: dict[str, HandlerSpec] = {}
        for spec in specs:
            if spec.job_type in self._specs:
                raise ValueError(f"duplicate handler for {spec.job_type}")
            self._specs[spec.job_type] = spec

    def manifest(self) -> list[dict[str, Any]]:
        return [
            {
                "job_type": spec.job_type,
                "family": spec.family,
                "default_timeout_seconds": spec.timeout_seconds,
            }
            for spec in self._specs.values()
        ]

    def validate(self, job: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(job, Mapping):
            raise ValueError("job must be a JSON object")
        if str(job.get("version")) != JOB_CONTRACT_VERSION:
            raise ValueError("job must use the v4.2.0 contract")
        job_type = str(job.get("job_type", ""))
        spec = self._specs.get(job_type)
        if spec is None:
            raise ValueError("job_type has no registered v4.2.2 handler")
        payload = spec.validator(_mapping(job.get("payload"), "payload"))
        supplied_timeout = job.get("payload", {}).get("timeout_seconds")
        timeout = (
            spec.timeout_seconds
            if supplied_timeout is None
            else _integer(
                supplied_timeout,
                "timeout_seconds",
                1,
                spec.timeout_seconds,
            )
        )
        return {
            "version": VERSION,
            "job_type": spec.job_type,
            "family": spec.family,
            "timeout_seconds": timeout,
            "payload": payload,
        }

    def execute(
        self,
        job: Mapping[str, Any],
        context: "ExecutionContext",
    ) -> Any:
        validated = self.validate(job)
        spec = self._specs[validated["job_type"]]
        context.checkpoint()
        result = spec.handler(validated["payload"], context)
        context.checkpoint()
        _canonical_json(result, "result", MAX_RESULT_BYTES)
        return result


@dataclass
class ExecutionContext:
    job_id: str
    deadline: float
    cancellation_probe: Callable[[str], bool]
    clock: Callable[[], float] = time.monotonic

    def checkpoint(self) -> None:
        if self.cancellation_probe(self.job_id):
            raise ExecutionCancelled("execution cancelled")
        if self.clock() >= self.deadline:
            raise ExecutionTimedOut("execution timed out")


def normalize_cancellation_request(
    job_id: Any,
    *,
    requested_at: Any,
    reason: Any = "cancelled by request",
) -> dict[str, Any]:
    normalized_job_id = _text(job_id, "job_id", 128)
    if not normalized_job_id.startswith("job_"):
        raise ValueError("job_id must use the v4.2 job identity")
    timestamp = _number(requested_at, "requested_at", 0, 99_999_999_999)
    normalized_reason = _text(reason, "reason", 500)
    identity = hashlib.sha256(
        f"{normalized_job_id}:{timestamp:.6f}:{normalized_reason}".encode("utf-8")
    ).hexdigest()[:24]
    return {
        "version": VERSION,
        "cancellation_id": f"cancel_{identity}",
        "job_id": normalized_job_id,
        "requested_at": round(timestamp, 6),
        "reason": normalized_reason,
    }


def normalize_result_record(job: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(job, Mapping):
        raise ValueError("job must be a JSON object")
    status = str(job.get("status", ""))
    if status not in {"succeeded", "failed", "cancelled"}:
        raise ValueError("result persistence requires a terminal job")
    job_id = _text(job.get("job_id"), "job_id", 128)
    result_raw = _canonical_json(job.get("result"), "result", MAX_RESULT_BYTES)
    error = str(job.get("error") or "")
    record = {
        "version": VERSION,
        "job_contract_version": JOB_CONTRACT_VERSION,
        "job_id": job_id,
        "job_type": str(job.get("job_type", "")),
        "status": status,
        "attempt": _integer(job.get("attempt"), "attempt", 1, 10),
        "payload_digest": str(job.get("payload_digest", "")),
        "completed_at": job.get("completed_at"),
        "result": json.loads(result_raw),
        "error": error or None,
    }
    canonical = _canonical_json(
        record,
        "result record",
        MAX_RESULT_RECORD_BYTES,
    )
    record["result_digest"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    _canonical_json(record, "result record", MAX_RESULT_RECORD_BYTES)
    return record


class InMemoryExecutionStore:
    """Thread-safe development result and cancellation store."""

    backend = "memory"

    def __init__(self) -> None:
        self._results: dict[str, dict[str, Any]] = {}
        self._cancellations: dict[str, dict[str, Any]] = {}
        self._lock = threading.RLock()

    def persist(self, job: Mapping[str, Any]) -> dict[str, Any]:
        record = normalize_result_record(job)
        with self._lock:
            existing = self._results.get(record["job_id"])
            if existing is not None:
                if existing == record:
                    return {"created": False, "record": deepcopy(existing)}
                if int(existing["attempt"]) >= int(record["attempt"]):
                    raise ValueError("result conflicts with an existing attempt")
            self._results[record["job_id"]] = record
            return {"created": True, "record": deepcopy(record)}

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            result = self._results.get(str(job_id))
            return deepcopy(result) if result is not None else None

    def request_cancellation(
        self,
        job_id: str,
        *,
        requested_at: float,
        reason: str = "cancelled by request",
    ) -> dict[str, Any]:
        request = normalize_cancellation_request(
            job_id,
            requested_at=requested_at,
            reason=reason,
        )
        with self._lock:
            self._cancellations[request["job_id"]] = request
        return deepcopy(request)

    def is_cancelled(self, job_id: str) -> bool:
        with self._lock:
            return str(job_id) in self._cancellations

    def clear_cancellation(self, job_id: str) -> bool:
        with self._lock:
            return self._cancellations.pop(str(job_id), None) is not None


class RedisExecutionStore:
    """Redis-backed idempotent results and cancellation requests."""

    backend = "redis"

    _PERSIST_SCRIPT = """
local existing = redis.call('HGET', KEYS[1], ARGV[1])
if existing then
  if existing == ARGV[2] then
    return 0
  end
  local old = cjson.decode(existing)
  local new = cjson.decode(ARGV[2])
  if tonumber(old.attempt) >= tonumber(new.attempt) then
    return -1
  end
end
redis.call('HSET', KEYS[1], ARGV[1], ARGV[2])
redis.call('EXPIRE', KEYS[1], ARGV[3])
return 1
"""

    def __init__(
        self,
        client: Any,
        *,
        key_prefix: str = "nfl:v42",
        result_ttl_seconds: int = 7 * 24 * 60 * 60,
    ) -> None:
        self.client = client
        self.key_prefix = _text(key_prefix, "key_prefix", 128)
        self.results_key = f"{self.key_prefix}:execution-results"
        self.cancellations_key = f"{self.key_prefix}:execution-cancellations"
        self.result_ttl_seconds = _integer(
            result_ttl_seconds,
            "result_ttl_seconds",
            60,
            MAX_RESULT_TTL_SECONDS,
        )

    @staticmethod
    def _text_value(value: Any) -> str:
        return value.decode("utf-8") if isinstance(value, bytes) else str(value)

    def persist(self, job: Mapping[str, Any]) -> dict[str, Any]:
        record = normalize_result_record(job)
        raw = _canonical_json(
            record,
            "result record",
            MAX_RESULT_RECORD_BYTES,
        )
        state = int(
            self.client.eval(
                self._PERSIST_SCRIPT,
                1,
                self.results_key,
                record["job_id"],
                raw,
                self.result_ttl_seconds,
            )
        )
        if state == -1:
            raise ValueError("result conflicts with an existing attempt")
        return {"created": state == 1, "record": record}

    def get(self, job_id: str) -> dict[str, Any] | None:
        raw = self.client.hget(self.results_key, str(job_id))
        if raw is None:
            return None
        return json.loads(self._text_value(raw))

    def request_cancellation(
        self,
        job_id: str,
        *,
        requested_at: float,
        reason: str = "cancelled by request",
    ) -> dict[str, Any]:
        request = normalize_cancellation_request(
            job_id,
            requested_at=requested_at,
            reason=reason,
        )
        raw = _canonical_json(request, "cancellation", 4_096)
        self.client.hset(self.cancellations_key, request["job_id"], raw)
        self.client.expire(self.cancellations_key, self.result_ttl_seconds)
        return request

    def is_cancelled(self, job_id: str) -> bool:
        return bool(self.client.hexists(self.cancellations_key, str(job_id)))

    def clear_cancellation(self, job_id: str) -> bool:
        return bool(self.client.hdel(self.cancellations_key, str(job_id)))


def build_execution_store(
    redis_url: str | None = None,
    *,
    client: Any = None,
    allow_memory_fallback: bool = True,
    **kwargs: Any,
) -> InMemoryExecutionStore | RedisExecutionStore:
    configured_url = redis_url if redis_url is not None else os.getenv("REDIS_URL")
    if client is not None:
        return RedisExecutionStore(client, **kwargs)
    if configured_url:
        from redis import Redis

        redis_client = Redis.from_url(configured_url, decode_responses=True)
        redis_client.ping()
        return RedisExecutionStore(redis_client, **kwargs)
    if not allow_memory_fallback:
        raise RuntimeError("REDIS_URL is required when memory fallback is disabled")
    return InMemoryExecutionStore()


@contextmanager
def _hard_timeout(seconds: float):
    """Interrupt Python handlers on POSIX main threads; otherwise stay cooperative."""
    if (
        seconds <= 0
        or not hasattr(signal, "setitimer")
        or threading.current_thread() is not threading.main_thread()
    ):
        yield
        return

    def alarm_handler(_signum, _frame):
        raise ExecutionTimedOut("execution timed out")

    previous = signal.getsignal(signal.SIGALRM)
    signal.signal(signal.SIGALRM, alarm_handler)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


class TypedWorker:
    """Claim, execute, persist, and acknowledge allowlisted v4.2 jobs."""

    def __init__(
        self,
        transport: Any,
        store: Any,
        worker_id: str,
        *,
        registry: TypedHandlerRegistry | None = None,
        monotonic_clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
    ) -> None:
        self.transport = transport
        self.store = store
        self.worker_id = _text(worker_id, "worker_id", 80)
        self.registry = registry or TypedHandlerRegistry()
        self.monotonic_clock = monotonic_clock
        self.wall_clock = wall_clock

    def run_once(
        self,
        *,
        count: int = 1,
        block_ms: int = 0,
    ) -> list[dict[str, Any]]:
        limit = _integer(count, "count", 1, MAX_WORKER_BATCH_SIZE)
        claims = self.transport.claim(
            self.worker_id,
            count=limit,
            block_ms=block_ms,
        )
        outcomes = []
        for claim in claims:
            outcomes.append(self._execute_claim(claim))
        return outcomes

    def _execute_claim(self, claim: Mapping[str, Any]) -> dict[str, Any]:
        message_id = _text(claim.get("message_id"), "message_id", 128)
        job = _mapping(claim.get("job"), "job")
        try:
            validated = self.registry.validate(job)
            timeout = float(validated["timeout_seconds"])
            started = self.monotonic_clock()
            context = ExecutionContext(
                job_id=str(job["job_id"]),
                deadline=started + timeout,
                cancellation_probe=self.store.is_cancelled,
                clock=self.monotonic_clock,
            )
            with _hard_timeout(timeout):
                result = self.registry.execute(job, context)
            terminal = transition_job(
                job,
                "succeeded",
                now=self.wall_clock(),
                result=result,
            )
        except ExecutionCancelled as exc:
            terminal = transition_job(
                job,
                "cancelled",
                now=self.wall_clock(),
                error=str(exc),
            )
        except ExecutionTimedOut as exc:
            terminal = transition_job(
                job,
                "failed",
                now=self.wall_clock(),
                error=str(exc),
            )
        except Exception as exc:  # noqa: BLE001
            message = str(exc).strip() or exc.__class__.__name__
            terminal = transition_job(
                job,
                "failed",
                now=self.wall_clock(),
                error=f"{exc.__class__.__name__}: {message}",
            )

        persisted = self.store.persist(terminal)
        acknowledgement = self.transport.acknowledge(
            message_id,
            self.worker_id,
            terminal,
        )
        self.store.clear_cancellation(str(terminal["job_id"]))
        return {
            "message_id": message_id,
            "job": terminal,
            "result_persisted": True,
            "result_created": bool(persisted["created"]),
            "acknowledged": bool(acknowledgement["acknowledged"]),
        }


def execution_manifest() -> dict[str, Any]:
    registry = TypedHandlerRegistry()
    return {
        "version": VERSION,
        "job_contract_version": JOB_CONTRACT_VERSION,
        "transport_version": TRANSPORT_VERSION,
        "name": "Distributed Execution",
        "features": {
            "typed_handlers": True,
            "bounded_timeouts": True,
            "cooperative_cancellation": True,
            "posix_hard_timeouts": True,
            "idempotent_result_persistence": True,
            "redis_result_store": True,
            "in_memory_result_store": True,
        },
        "handlers": registry.manifest(),
        "result_backends": ["redis", "memory"],
        "limits": {
            "max_timeout_seconds": MAX_TIMEOUT_SECONDS,
            "max_worker_batch_size": MAX_WORKER_BATCH_SIZE,
            "max_result_bytes": MAX_RESULT_BYTES,
            "max_result_record_bytes": MAX_RESULT_RECORD_BYTES,
            "max_result_ttl_seconds": MAX_RESULT_TTL_SECONDS,
        },
        "next_increment": "v4.2.3 Cache, observability, and operations",
    }
