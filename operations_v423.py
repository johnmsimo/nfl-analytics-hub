"""Distributed cache and operations contracts for NFL Analytics Hub v4.2.3."""

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

VERSION = "4.2.3"
JOB_CONTRACT_VERSION = "4.2.0"
TRANSPORT_VERSION = "4.2.1"
EXECUTION_VERSION = "4.2.2"
DEFAULT_CACHE_TTL_SECONDS = 15 * 60
MAX_CACHE_TTL_SECONDS = 30 * 24 * 60 * 60
MAX_CACHE_VALUE_BYTES = 256 * 1024
MAX_INVALIDATION_KEYS = 1_000
MAX_INVALIDATION_EVENTS = 1_000
MAX_TAGS = 20

_IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


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
        raise ValueError(f"{field} must use lowercase letters, numbers, dots, dashes, or underscores")
    return result


def _text(value: Any, field: str, maximum: int) -> str:
    result = str(value or "").strip()
    if not result:
        raise ValueError(f"{field} is required")
    if len(result) > maximum:
        raise ValueError(f"{field} cannot exceed {maximum} characters")
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


def _tags(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str | bytes) or not isinstance(value, Sequence):
        raise ValueError("tags must be a list")
    if len(value) > MAX_TAGS:
        raise ValueError(f"tags cannot contain more than {MAX_TAGS} items")
    result: list[str] = []
    for item in value:
        tag = _identifier(item, "tag")
        if tag not in result:
            result.append(tag)
    return sorted(result)


def _logical_keys(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str | bytes) or not isinstance(value, Sequence):
        raise ValueError("keys must be a list")
    if len(value) > MAX_INVALIDATION_KEYS:
        raise ValueError(f"keys cannot contain more than {MAX_INVALIDATION_KEYS} items")
    result: list[str] = []
    for item in value:
        key = _text(item, "key", 256)
        if key not in result:
            result.append(key)
    return sorted(result)


def normalize_cache_key(
    namespace: Any,
    logical_key: Any,
    *,
    cache_version: Any = 1,
    key_prefix: str = "nfl:v42",
) -> dict[str, Any]:
    """Build a deterministic cache address without embedding caller data in Redis keys."""
    normalized_namespace = _identifier(namespace, "namespace")
    normalized_key = _text(logical_key, "key", 256)
    normalized_version = _integer(cache_version, "cache_version", 1, 10_000)
    normalized_prefix = _text(key_prefix, "key_prefix", 128).rstrip(":")
    digest = hashlib.sha256(normalized_key.encode("utf-8")).hexdigest()
    return {
        "version": VERSION,
        "namespace": normalized_namespace,
        "cache_version": normalized_version,
        "logical_key": normalized_key,
        "key_digest": digest,
        "storage_key": (
            f"{normalized_prefix}:cache:{normalized_namespace}:v{normalized_version}:{digest[:32]}"
        ),
    }


def normalize_invalidation_event(
    payload: Mapping[str, Any],
    *,
    occurred_at: float | None = None,
    sequence: Any = 1,
) -> dict[str, Any]:
    """Normalize one provider-neutral cache invalidation event."""
    if not isinstance(payload, Mapping):
        raise ValueError("invalidation must be a JSON object")
    namespace = _identifier(payload.get("namespace"), "namespace")
    cache_version = _integer(
        payload.get("cache_version", 1),
        "cache_version",
        1,
        10_000,
    )
    keys = _logical_keys(payload.get("keys"))
    tags = _tags(payload.get("tags"))
    invalidate_namespace = bool(payload.get("invalidate_namespace", False))
    if not keys and not tags and not invalidate_namespace:
        raise ValueError("invalidation requires keys, tags, or invalidate_namespace")
    normalized_sequence = _integer(sequence, "sequence", 1, 1_000_000_000)
    timestamp = _timestamp(
        time.time() if occurred_at is None else occurred_at,
        "occurred_at",
    )
    reason = _text(
        payload.get("reason", "data changed"),
        "reason",
        256,
    )
    identity = _canonical_json(
        {
            "namespace": namespace,
            "cache_version": cache_version,
            "keys": keys,
            "tags": tags,
            "invalidate_namespace": invalidate_namespace,
            "reason": reason,
            "sequence": normalized_sequence,
            "occurred_at": timestamp,
        },
        "invalidation",
        64 * 1024,
    )
    event_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
    return {
        "version": VERSION,
        "event_id": f"cache_evt_{event_id}",
        "event_type": "cache.invalidated",
        "namespace": namespace,
        "cache_version": cache_version,
        "keys": keys,
        "tags": tags,
        "invalidate_namespace": invalidate_namespace,
        "reason": reason,
        "sequence": normalized_sequence,
        "occurred_at": timestamp,
    }


def normalize_cache_record(
    namespace: Any,
    logical_key: Any,
    value: Any,
    *,
    cache_version: Any = 1,
    ttl_seconds: Any = DEFAULT_CACHE_TTL_SECONDS,
    tags: Any = None,
    created_at: float | None = None,
    key_prefix: str = "nfl:v42",
) -> dict[str, Any]:
    """Build a bounded cache record for either backend."""
    address = normalize_cache_key(
        namespace,
        logical_key,
        cache_version=cache_version,
        key_prefix=key_prefix,
    )
    raw_value = _canonical_json(value, "value", MAX_CACHE_VALUE_BYTES)
    ttl = _integer(
        ttl_seconds,
        "ttl_seconds",
        1,
        MAX_CACHE_TTL_SECONDS,
    )
    timestamp = _timestamp(
        time.time() if created_at is None else created_at,
        "created_at",
    )
    return {
        **address,
        "value": json.loads(raw_value),
        "tags": _tags(tags),
        "created_at": timestamp,
        "expires_at": round(timestamp + ttl, 6),
        "ttl_seconds": ttl,
    }


class InMemoryDistributedCache:
    """Thread-safe development cache with the v4.2.3 invalidation contract."""

    backend = "memory"
    durable = False

    def __init__(self, *, key_prefix: str = "nfl:v42") -> None:
        self.key_prefix = _text(key_prefix, "key_prefix", 128).rstrip(":")
        self._records: dict[str, dict[str, Any]] = {}
        self._namespace_index: dict[str, set[str]] = {}
        self._tag_index: dict[tuple[str, str], set[str]] = {}
        self._events: list[dict[str, Any]] = []
        self._lock = threading.RLock()

    def set(
        self,
        namespace: Any,
        logical_key: Any,
        value: Any,
        **kwargs: Any,
    ) -> dict[str, Any]:
        record = normalize_cache_record(
            namespace,
            logical_key,
            value,
            key_prefix=self.key_prefix,
            **kwargs,
        )
        storage_key = record["storage_key"]
        with self._lock:
            self._remove(storage_key)
            self._records[storage_key] = record
            self._namespace_index.setdefault(record["namespace"], set()).add(storage_key)
            for tag in record["tags"]:
                self._tag_index.setdefault((record["namespace"], tag), set()).add(storage_key)
        return deepcopy(record)

    def get(
        self,
        namespace: Any,
        logical_key: Any,
        *,
        cache_version: Any = 1,
        now: float | None = None,
    ) -> Any:
        address = normalize_cache_key(
            namespace,
            logical_key,
            cache_version=cache_version,
            key_prefix=self.key_prefix,
        )
        timestamp = _timestamp(time.time() if now is None else now, "now")
        with self._lock:
            record = self._records.get(address["storage_key"])
            if record is None:
                return None
            if record["expires_at"] <= timestamp:
                self._remove(address["storage_key"])
                return None
            return deepcopy(record["value"])

    def invalidate(self, event: Mapping[str, Any]) -> dict[str, Any]:
        normalized = normalize_invalidation_event(
            event,
            occurred_at=event.get("occurred_at"),
            sequence=event.get("sequence", 1),
        )
        with self._lock:
            candidates = self._candidate_keys(normalized)
            truncated = len(candidates) > MAX_INVALIDATION_KEYS
            removed = 0
            for storage_key in sorted(candidates)[:MAX_INVALIDATION_KEYS]:
                if storage_key in self._records:
                    self._remove(storage_key)
                    removed += 1
            self._events.append(normalized)
            self._events = self._events[-MAX_INVALIDATION_EVENTS:]
        return {
            "event": deepcopy(normalized),
            "invalidated": removed,
            "truncated": truncated,
        }

    def recent_invalidations(self, limit: Any = 50) -> list[dict[str, Any]]:
        bounded = _integer(limit, "limit", 1, 100)
        with self._lock:
            return deepcopy(list(reversed(self._events[-bounded:])))

    def health(self) -> dict[str, Any]:
        return {
            "component": "distributed_cache",
            "backend": self.backend,
            "healthy": True,
            "durable": self.durable,
        }

    def _candidate_keys(self, event: Mapping[str, Any]) -> set[str]:
        namespace = str(event["namespace"])
        candidates: set[str] = set()
        if event["invalidate_namespace"]:
            candidates.update(self._namespace_index.get(namespace, set()))
        for logical_key in event["keys"]:
            candidates.add(
                normalize_cache_key(
                    namespace,
                    logical_key,
                    cache_version=event["cache_version"],
                    key_prefix=self.key_prefix,
                )["storage_key"]
            )
        for tag in event["tags"]:
            candidates.update(self._tag_index.get((namespace, tag), set()))
        return candidates

    def _remove(self, storage_key: str) -> None:
        record = self._records.pop(storage_key, None)
        if record is None:
            return
        namespace_keys = self._namespace_index.get(record["namespace"])
        if namespace_keys is not None:
            namespace_keys.discard(storage_key)
            if not namespace_keys:
                self._namespace_index.pop(record["namespace"], None)
        for tag in record["tags"]:
            tag_keys = self._tag_index.get((record["namespace"], tag))
            if tag_keys is not None:
                tag_keys.discard(storage_key)
                if not tag_keys:
                    self._tag_index.pop((record["namespace"], tag), None)


class RedisDistributedCache:
    """Redis-backed cache using bounded indexes instead of key-space scans."""

    backend = "redis"
    durable = True

    def __init__(self, client: Any, *, key_prefix: str = "nfl:v42") -> None:
        self.client = client
        self.key_prefix = _text(key_prefix, "key_prefix", 128).rstrip(":")
        self.events_key = f"{self.key_prefix}:cache-invalidations"

    @staticmethod
    def _decode(value: Any) -> str:
        return value.decode("utf-8") if isinstance(value, bytes) else str(value)

    def _namespace_index(self, namespace: str) -> str:
        return f"{self.key_prefix}:cache-index:namespace:{namespace}"

    def _tag_index(self, namespace: str, tag: str) -> str:
        return f"{self.key_prefix}:cache-index:tag:{namespace}:{tag}"

    def set(
        self,
        namespace: Any,
        logical_key: Any,
        value: Any,
        **kwargs: Any,
    ) -> dict[str, Any]:
        record = normalize_cache_record(
            namespace,
            logical_key,
            value,
            key_prefix=self.key_prefix,
            **kwargs,
        )
        raw = _canonical_json(record, "cache record", MAX_CACHE_VALUE_BYTES + 4_096)
        indexes = [self._namespace_index(record["namespace"])] + [
            self._tag_index(record["namespace"], tag) for tag in record["tags"]
        ]
        with self.client.pipeline(transaction=True) as pipe:
            pipe.set(record["storage_key"], raw, ex=record["ttl_seconds"])
            for index in indexes:
                pipe.sadd(index, record["storage_key"])
                pipe.expire(index, MAX_CACHE_TTL_SECONDS)
            pipe.execute()
        return record

    def get(
        self,
        namespace: Any,
        logical_key: Any,
        *,
        cache_version: Any = 1,
        now: float | None = None,
    ) -> Any:
        del now
        address = normalize_cache_key(
            namespace,
            logical_key,
            cache_version=cache_version,
            key_prefix=self.key_prefix,
        )
        raw = self.client.get(address["storage_key"])
        if raw is None:
            return None
        return json.loads(self._decode(raw))["value"]

    def invalidate(self, event: Mapping[str, Any]) -> dict[str, Any]:
        normalized = normalize_invalidation_event(
            event,
            occurred_at=event.get("occurred_at"),
            sequence=event.get("sequence", 1),
        )
        candidates: set[str] = set()
        namespace = normalized["namespace"]
        if normalized["invalidate_namespace"]:
            candidates.update(self._bounded_members(self._namespace_index(namespace)))
        for logical_key in normalized["keys"]:
            candidates.add(
                normalize_cache_key(
                    namespace,
                    logical_key,
                    cache_version=normalized["cache_version"],
                    key_prefix=self.key_prefix,
                )["storage_key"]
            )
        for tag in normalized["tags"]:
            candidates.update(self._bounded_members(self._tag_index(namespace, tag)))
        ordered = sorted(candidates)
        targets = ordered[:MAX_INVALIDATION_KEYS]
        index_removals: dict[str, set[str]] = {}
        if targets:
            for storage_key, raw_record in zip(
                targets,
                self.client.mget(targets),
                strict=True,
            ):
                if raw_record is None:
                    index_removals.setdefault(
                        self._namespace_index(namespace),
                        set(),
                    ).add(storage_key)
                    for tag in normalized["tags"]:
                        index_removals.setdefault(
                            self._tag_index(namespace, tag),
                            set(),
                        ).add(storage_key)
                    continue
                record = json.loads(self._decode(raw_record))
                record_namespace = str(record["namespace"])
                index_removals.setdefault(
                    self._namespace_index(record_namespace),
                    set(),
                ).add(storage_key)
                for tag in record.get("tags", []):
                    index_removals.setdefault(
                        self._tag_index(record_namespace, str(tag)),
                        set(),
                    ).add(storage_key)
        raw_event = _canonical_json(normalized, "invalidation", 64 * 1024)
        with self.client.pipeline(transaction=True) as pipe:
            if targets:
                pipe.delete(*targets)
                for index, members in index_removals.items():
                    pipe.srem(index, *sorted(members))
            pipe.xadd(
                self.events_key,
                {"event": raw_event},
                maxlen=MAX_INVALIDATION_EVENTS,
                approximate=True,
            )
            results = pipe.execute()
        removed = int(results[0]) if targets else 0
        return {
            "event": normalized,
            "invalidated": removed,
            "truncated": len(ordered) > MAX_INVALIDATION_KEYS,
        }

    def recent_invalidations(self, limit: Any = 50) -> list[dict[str, Any]]:
        bounded = _integer(limit, "limit", 1, 100)
        rows = self.client.xrevrange(self.events_key, count=bounded)
        events = []
        for _, fields in rows:
            raw = fields.get("event") or fields.get(b"event")
            if raw is not None:
                events.append(json.loads(self._decode(raw)))
        return events

    def health(self) -> dict[str, Any]:
        healthy = bool(self.client.ping())
        return {
            "component": "distributed_cache",
            "backend": self.backend,
            "healthy": healthy,
            "durable": self.durable,
        }

    def _bounded_members(self, index: str) -> set[str]:
        members: set[str] = set()
        cursor: int | str = 0
        while True:
            cursor, batch = self.client.sscan(
                index,
                cursor=cursor,
                count=min(100, MAX_INVALIDATION_KEYS),
            )
            members.update(self._decode(item) for item in batch)
            if len(members) > MAX_INVALIDATION_KEYS or int(cursor) == 0:
                break
        return members


def build_distributed_cache(
    redis_url: str | None = None,
    *,
    client: Any = None,
    allow_memory_fallback: bool = True,
    **kwargs: Any,
) -> InMemoryDistributedCache | RedisDistributedCache:
    """Build the Redis cache when configured, otherwise the development fallback."""
    configured_url = redis_url if redis_url is not None else os.getenv("REDIS_URL")
    if client is not None:
        return RedisDistributedCache(client, **kwargs)
    if configured_url:
        from redis import Redis

        redis_client = Redis.from_url(configured_url, decode_responses=True)
        redis_client.ping()
        return RedisDistributedCache(redis_client, **kwargs)
    if not allow_memory_fallback:
        raise RuntimeError("REDIS_URL is required when memory fallback is disabled")
    return InMemoryDistributedCache(**kwargs)


def component_health(component: Any, name: str) -> dict[str, Any]:
    """Return a consistent health record for cache, transport, or result stores."""
    backend = str(getattr(component, "backend", "unknown"))
    durable = backend == "redis"
    try:
        if hasattr(component, "health"):
            result = dict(component.health())
            result.setdefault("component", name)
            result.setdefault("backend", backend)
            result.setdefault("durable", durable)
            result["healthy"] = bool(result.get("healthy"))
            return result
        client = getattr(component, "client", None)
        healthy = bool(client.ping()) if client is not None else True
        return {
            "component": name,
            "backend": backend,
            "healthy": healthy,
            "durable": durable,
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "component": name,
            "backend": backend,
            "healthy": False,
            "durable": durable,
            "error": str(exc)[:300],
        }


def operations_manifest() -> dict[str, Any]:
    return {
        "version": VERSION,
        "job_contract_version": JOB_CONTRACT_VERSION,
        "transport_version": TRANSPORT_VERSION,
        "execution_version": EXECUTION_VERSION,
        "name": "Cache and Operations",
        "features": {
            "namespaced_distributed_cache": True,
            "bounded_cache_values": True,
            "invalidation_events": True,
            "queue_depth_metrics": True,
            "queue_latency_metrics": True,
            "dead_letter_inspection": True,
            "component_health_checks": True,
            "horizontal_scaling_guidance": True,
        },
        "cache_backends": ["redis", "memory"],
        "limits": {
            "default_cache_ttl_seconds": DEFAULT_CACHE_TTL_SECONDS,
            "max_cache_ttl_seconds": MAX_CACHE_TTL_SECONDS,
            "max_cache_value_bytes": MAX_CACHE_VALUE_BYTES,
            "max_invalidation_keys": MAX_INVALIDATION_KEYS,
            "max_tags": MAX_TAGS,
        },
        "scaling": {
            "web": "stateless replicas share Redis cache and job state",
            "workers": "scale replicas from queue depth and p95 claim latency",
            "scheduler": "run one scheduler leader unless leader election is configured",
            "redis": "required for multi-replica durability and coordination",
            "shutdown": "stop claims, finish the active lease, then terminate",
        },
        "next_increment": None,
    }
