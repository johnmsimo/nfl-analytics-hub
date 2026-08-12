"""Redis-backed decision delivery intake for NFL Analytics Hub v4.5.0.

This release owns durable, idempotent job intake and status inspection. It does
not perform outbound network delivery; the worker/dispatch contract is a later
v4.5 increment.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
import uuid
from collections import OrderedDict
from typing import Any
from urllib.parse import urlparse

from flask import current_app

VERSION = "4.5.0"
DEFAULT_TTL_SECONDS = 7 * 24 * 60 * 60
MAX_TTL_SECONDS = 30 * 24 * 60 * 60
MAX_PAYLOAD_BYTES = 128 * 1024
MAX_EVENT_TYPE_LENGTH = 80
MAX_DESTINATION_LENGTH = 2048
MAX_LIST_LIMIT = 100
DISPATCH_VERSION = "4.5.1"


class DeliveryConflictError(ValueError):
    """An idempotency key was reused with different request content."""


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def request_digest(
    event_type: str,
    destination: str,
    payload: Any,
    workspace_id: str | None = None,
) -> str:
    body = _canonical(
        {
            "event_type": event_type,
            "destination": destination,
            "payload": payload,
            "workspace_id": workspace_id,
        }
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(body).hexdigest()


def _timestamp(value: Any = None) -> float:
    result = time.time() if value is None else float(value)
    if result != result or result in {float("inf"), float("-inf")}:
        raise ValueError("timestamp must be finite")
    return round(result, 6)


def _ttl() -> int:
    raw = os.getenv("V45_DELIVERY_TTL_SECONDS")
    try:
        value = DEFAULT_TTL_SECONDS if raw is None else int(raw)
    except ValueError as exc:
        raise RuntimeError("V45_DELIVERY_TTL_SECONDS must be an integer") from exc
    if not 3600 <= value <= MAX_TTL_SECONDS:
        raise RuntimeError(f"V45_DELIVERY_TTL_SECONDS must be between 3600 and {MAX_TTL_SECONDS}")
    return value


def validate_request(
    event_type: Any,
    destination: Any,
    payload: Any,
) -> tuple[str, str, Any, int]:
    normalized_event = str(event_type or "").strip().lower()
    if not normalized_event or len(normalized_event) > MAX_EVENT_TYPE_LENGTH:
        raise ValueError("event_type must contain 1-80 characters")
    if any(char not in "abcdefghijklmnopqrstuvwxyz0123456789._-" for char in normalized_event):
        raise ValueError("event_type contains unsupported characters")
    normalized_destination = str(destination or "").strip()
    parsed = urlparse(normalized_destination)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or len(normalized_destination) > MAX_DESTINATION_LENGTH
    ):
        raise ValueError("destination must be an HTTPS URL without embedded credentials")
    try:
        encoded = _canonical(payload).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("payload must be JSON serializable") from exc
    if len(encoded) > MAX_PAYLOAD_BYTES:
        raise ValueError(f"payload cannot exceed {MAX_PAYLOAD_BYTES} bytes")
    return normalized_event, normalized_destination, payload, len(encoded)


def _record(
    *,
    delivery_id: str,
    organization_id: str,
    api_key_id: str,
    idempotency_key: str,
    event_type: str,
    destination: str,
    payload: Any,
    digest: str,
    created_at: float,
    workspace_id: str | None = None,
) -> dict[str, Any]:
    return {
        "version": VERSION,
        "delivery_id": delivery_id,
        "organization_id": organization_id,
        "api_key_id": api_key_id,
        "workspace_id": workspace_id,
        "idempotency_key": idempotency_key,
        "event_type": event_type,
        "destination": destination,
        "payload": payload,
        "payload_digest": digest,
        "status": "queued",
        "attempts": 0,
        "created_at": created_at,
        "updated_at": created_at,
    }


class InMemoryDeliveryBackend:
    """Development-only backend with the same contract as Redis."""

    backend = "memory"
    distributed = False

    def __init__(self) -> None:
        self._records: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._idempotency: dict[tuple[str, str], tuple[str, str, float]] = {}
        self._lock = threading.RLock()

    def enqueue(
        self,
        organization_id: str,
        api_key_id: str,
        idempotency_key: str,
        event_type: Any,
        destination: Any,
        payload: Any,
        *,
        now: Any = None,
        workspace_id: str | None = None,
    ) -> dict[str, Any]:
        event, target, body, _ = validate_request(event_type, destination, payload)
        request_id = str(idempotency_key or "").strip()
        if not request_id or len(request_id) > 200:
            raise ValueError("Idempotency-Key must contain 1-200 characters")
        digest = request_digest(event, target, body, workspace_id)
        timestamp = _timestamp(now)
        with self._lock:
            existing = self._idempotency.get((organization_id, request_id))
            if existing and existing[2] > timestamp:
                if existing[1] != digest:
                    raise DeliveryConflictError(
                        "Idempotency-Key was already used for different delivery content"
                    )
                return {**self._records[existing[0]], "replayed": True}
            delivery_id = "delivery_" + uuid.uuid4().hex[:24]
            result = _record(
                delivery_id=delivery_id,
                organization_id=organization_id,
                api_key_id=api_key_id,
                idempotency_key=request_id,
                event_type=event,
                destination=target,
                payload=body,
                digest=digest,
                created_at=timestamp,
                workspace_id=workspace_id,
            )
            self._records[delivery_id] = result
            self._idempotency[(organization_id, request_id)] = (
                delivery_id,
                digest,
                timestamp + _ttl(),
            )
            return {**result, "replayed": False}

    def get(self, organization_id: str, delivery_id: str) -> dict[str, Any] | None:
        with self._lock:
            result = self._records.get(delivery_id)
            if not result or result["organization_id"] != organization_id:
                return None
            return dict(result)

    def list(self, organization_id: str, limit: int = 50) -> list[dict[str, Any]]:
        if not 1 <= limit <= MAX_LIST_LIMIT:
            raise ValueError(f"limit must be between 1 and {MAX_LIST_LIMIT}")
        with self._lock:
            return [
                dict(item)
                for item in reversed(list(self._records.values()))
                if item["organization_id"] == organization_id
            ][:limit]


class RedisDeliveryBackend:
    backend = "redis"
    distributed = True

    def __init__(self, client: Any) -> None:
        self.client = client
        self.key_prefix = os.getenv("V45_DELIVERY_KEY_PREFIX", "nfl:v450:delivery")
        self.ttl_seconds = _ttl()

    def _status_key(self, delivery_id: str) -> str:
        return f"{self.key_prefix}:status:{delivery_id}"

    def _idempotency_key(self, organization_id: str, request_id: str) -> str:
        digest = hashlib.sha256(request_id.encode()).hexdigest()
        return f"{self.key_prefix}:idempotency:{organization_id}:{digest}"

    def enqueue(
        self,
        organization_id: str,
        api_key_id: str,
        idempotency_key: str,
        event_type: Any,
        destination: Any,
        payload: Any,
        *,
        now: Any = None,
        workspace_id: str | None = None,
    ) -> dict[str, Any]:
        event, target, body, _ = validate_request(event_type, destination, payload)
        request_id = str(idempotency_key or "").strip()
        if not request_id or len(request_id) > 200:
            raise ValueError("Idempotency-Key must contain 1-200 characters")
        digest = request_digest(event, target, body, workspace_id)
        idem_key = self._idempotency_key(organization_id, request_id)
        existing = self.client.get(idem_key)
        if existing:
            reference = json.loads(existing)
            if reference["payload_digest"] != digest:
                raise DeliveryConflictError("Idempotency-Key was already used for different delivery content")
            stored = self.get(organization_id, reference["delivery_id"])
            if stored is not None:
                stored["replayed"] = True
                return stored
        timestamp = _timestamp(now)
        delivery_id = "delivery_" + uuid.uuid4().hex[:24]
        result = _record(
            delivery_id=delivery_id,
            organization_id=organization_id,
            api_key_id=api_key_id,
            idempotency_key=request_id,
            event_type=event,
            destination=target,
            payload=body,
            digest=digest,
            created_at=timestamp,
            workspace_id=workspace_id,
        )
        status_key = self._status_key(delivery_id)
        reference = {"delivery_id": delivery_id, "payload_digest": digest}
        claimed = self.client.set(
            idem_key,
            _canonical(reference),
            nx=True,
            ex=self.ttl_seconds,
        )
        if not claimed:
            existing = self.client.get(idem_key)
            if existing:
                reference = json.loads(existing)
                if reference["payload_digest"] != digest:
                    raise DeliveryConflictError(
                        "Idempotency-Key was already used for different delivery content"
                    )
                stored = self.get(organization_id, reference["delivery_id"])
                if stored is not None:
                    stored["replayed"] = True
                    return stored
            raise RuntimeError("delivery idempotency claim could not be resolved")
        serialized = _canonical(result)
        try:
            pipe = self.client.pipeline(transaction=True)
            pipe.set(status_key, serialized, ex=self.ttl_seconds)
            pipe.zadd(
                f"{self.key_prefix}:index:{organization_id}",
                {delivery_id: timestamp},
            )
            pipe.expire(f"{self.key_prefix}:index:{organization_id}", self.ttl_seconds)
            pipe.xadd(
                f"{self.key_prefix}:stream",
                {"delivery": serialized},
                maxlen=10000,
                approximate=True,
            )
            pipe.execute()
        except Exception:
            self.client.delete(idem_key)
            raise
        return {**result, "replayed": False}

    def get(self, organization_id: str, delivery_id: str) -> dict[str, Any] | None:
        value = self.client.get(self._status_key(delivery_id))
        if not value:
            return None
        result = json.loads(value)
        return result if result.get("organization_id") == organization_id else None

    def list(self, organization_id: str, limit: int = 50) -> list[dict[str, Any]]:
        if not 1 <= limit <= MAX_LIST_LIMIT:
            raise ValueError(f"limit must be between 1 and {MAX_LIST_LIMIT}")
        ids = self.client.zrevrange(f"{self.key_prefix}:index:{organization_id}", 0, limit - 1)
        return [
            result for delivery_id in ids if (result := self.get(organization_id, delivery_id)) is not None
        ]


def get_delivery_backend() -> InMemoryDeliveryBackend | RedisDeliveryBackend:
    """Resolve the configured backend; production fails closed without Redis."""
    configured = current_app.extensions.get("v450_delivery_backend")
    if configured is not None:
        return configured
    redis_url = os.getenv("REDIS_URL")
    if redis_url:
        try:
            import redis

            client = redis.Redis.from_url(
                redis_url,
                decode_responses=True,
                socket_connect_timeout=2,
                socket_timeout=2,
            )
            client.ping()
            backend: InMemoryDeliveryBackend | RedisDeliveryBackend = RedisDeliveryBackend(client)
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError("v4.5 delivery Redis backend is unavailable") from exc
    elif os.getenv("APP_ENV", os.getenv("FLASK_ENV", "development")).lower() == "production":
        raise RuntimeError("REDIS_URL is required for production decision delivery")
    else:
        backend = InMemoryDeliveryBackend()
    current_app.extensions["v450_delivery_backend"] = backend
    return backend


def delivery_manifest() -> dict[str, Any]:
    return {
        "version": "4.5.3",
        "intake_version": VERSION,
        "dispatch_version": DISPATCH_VERSION,
        "operations_version": "4.5.3",
        "observability_version": "4.5.4",
        "status_values": ["queued", "dispatching", "delivered", "retrying", "failed", "dead_letter"],
        "intake_status": "queued",
        "outbound_delivery_enabled": True,
        "signed_dispatch_worker": True,
        "dispatch_statuses": ["queued", "dispatching", "delivered", "retrying", "failed", "dead_letter"],
        "idempotency_ttl_seconds": _ttl(),
        "max_payload_bytes": MAX_PAYLOAD_BYTES,
        "production_backend": "redis",
        "development_backend": "memory",
        "fail_closed_without_production_redis": True,
    }
