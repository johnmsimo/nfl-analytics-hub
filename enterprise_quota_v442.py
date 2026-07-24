"""Quota contracts and backends for NFL Analytics Hub v4.4.2."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import threading
import time
from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from flask import current_app

VERSION = "4.4.2"
MIN_WINDOW_SECONDS = 60
MAX_WINDOW_SECONDS = 86_400
MIN_QUOTA_LIMIT = 1
MAX_QUOTA_LIMIT = 1_000_000
DEFAULT_ORGANIZATION_LIMIT = 1_000
DEFAULT_CREDENTIAL_LIMIT = 100
DEFAULT_WINDOW_SECONDS = 60
IDEMPOTENCY_TTL_SECONDS = 86_400
MAX_REQUEST_BYTES = 128 * 1024

_ORGANIZATION_ID = re.compile(r"^org_[a-f0-9]{20}$")
_API_KEY_ID = re.compile(r"^apikey_[a-f0-9]{20}$")
_IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$")
_OPERATIONS = {
    "decision.brief",
    "decision.ensemble",
    "decision.scenario",
}


class QuotaExceededError(RuntimeError):
    """Raised when either organization or credential capacity is exhausted."""

    def __init__(self, decision: dict[str, Any]) -> None:
        super().__init__("enterprise decision quota exceeded")
        self.decision = decision


class IdempotencyConflictError(ValueError):
    """Raised when a key is reused for a different request."""


def _canonical_json(value: Any, field: str, maximum_bytes: int = MAX_REQUEST_BYTES) -> str:
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


def _integer(value: Any, field: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be an integer between {minimum} and {maximum}")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be an integer between {minimum} and {maximum}") from exc
    if result != value or not minimum <= result <= maximum:
        raise ValueError(f"{field} must be an integer between {minimum} and {maximum}")
    return result


def _timestamp(value: Any, field: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a finite non-negative number") from exc
    if not math.isfinite(result) or result < 0:
        raise ValueError(f"{field} must be a finite non-negative number")
    return round(result, 6)


def _organization_id(value: Any) -> str:
    result = str(value or "").strip().lower()
    if not _ORGANIZATION_ID.fullmatch(result):
        raise ValueError("organization_id must be a normalized v4.4 organization identity")
    return result


def _api_key_id(value: Any) -> str:
    result = str(value or "").strip().lower()
    if not _API_KEY_ID.fullmatch(result):
        raise ValueError("api_key_id must be a normalized v4.4 API-key identity")
    return result


def _actor(value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise ValueError("updated_by must be a JSON object")
    subject_type = str(value.get("type") or "").strip().lower()
    subject_id = str(value.get("id") or "").strip().lower()
    if subject_type not in {"user", "service"} or not subject_id or len(subject_id) > 160:
        raise ValueError("updated_by must contain a user or service type and a bounded id")
    return {"type": subject_type, "id": subject_id}


def _operation(value: Any) -> str:
    result = str(value or "").strip().lower()
    if result not in _OPERATIONS:
        raise ValueError("operation is not in the v4.4.2 public decision catalog")
    return result


def normalize_idempotency_key(value: Any) -> str:
    """Validate one client-controlled request identity."""
    result = str(value or "").strip()
    if not _IDEMPOTENCY_KEY.fullmatch(result):
        raise ValueError(
            "Idempotency-Key must contain 8-128 letters, numbers, dots, dashes, underscores, or colons"
        )
    return result


def request_digest(operation: Any, payload: Any) -> str:
    """Bind an idempotency key to one exact public decision request."""
    return _digest(
        {
            "operation": _operation(operation),
            "payload": payload,
        },
        "public decision request",
    )


def normalize_quota_policy(
    organization_id: Any,
    payload: Mapping[str, Any],
    *,
    updated_by: Any,
    updated_at: Any,
) -> dict[str, Any]:
    """Normalize one organization and per-credential fixed-window quota."""
    if not isinstance(payload, Mapping):
        raise ValueError("quota policy must be a JSON object")
    organization = _organization_id(organization_id)
    organization_limit = _integer(
        payload.get("organization_limit"),
        "organization_limit",
        MIN_QUOTA_LIMIT,
        MAX_QUOTA_LIMIT,
    )
    credential_limit = _integer(
        payload.get("credential_limit"),
        "credential_limit",
        MIN_QUOTA_LIMIT,
        MAX_QUOTA_LIMIT,
    )
    if credential_limit > organization_limit:
        raise ValueError("credential_limit cannot exceed organization_limit")
    body = {
        "organization_id": organization,
        "organization_limit": organization_limit,
        "credential_limit": credential_limit,
        "window_seconds": _integer(
            payload.get("window_seconds", DEFAULT_WINDOW_SECONDS),
            "window_seconds",
            MIN_WINDOW_SECONDS,
            MAX_WINDOW_SECONDS,
        ),
        "updated_by": _actor(updated_by),
        "updated_at": _timestamp(updated_at, "updated_at"),
    }
    return {
        "version": VERSION,
        **body,
        "policy_digest": _digest(body, "quota policy"),
    }


def _validated_quota_policy(value: Any, organization_id: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("stored quota policy must be a JSON object")
    if value.get("version") != VERSION:
        raise ValueError(f"stored quota policy must use version {VERSION}")
    normalized = normalize_quota_policy(
        organization_id,
        value,
        updated_by=value.get("updated_by"),
        updated_at=value.get("updated_at"),
    )
    if value.get("policy_digest") != normalized["policy_digest"]:
        raise ValueError("stored quota policy_digest does not match its content")
    return normalized


def default_quota_policy(
    organization_id: Any,
    *,
    organization_limit: Any = DEFAULT_ORGANIZATION_LIMIT,
    credential_limit: Any = DEFAULT_CREDENTIAL_LIMIT,
    window_seconds: Any = DEFAULT_WINDOW_SECONDS,
) -> dict[str, Any]:
    """Build the environment-backed policy used until an override is stored."""
    organization = _organization_id(organization_id)
    org_limit = _integer(
        organization_limit,
        "organization_limit",
        MIN_QUOTA_LIMIT,
        MAX_QUOTA_LIMIT,
    )
    key_limit = _integer(
        credential_limit,
        "credential_limit",
        MIN_QUOTA_LIMIT,
        MAX_QUOTA_LIMIT,
    )
    if key_limit > org_limit:
        raise ValueError("credential_limit cannot exceed organization_limit")
    body = {
        "organization_id": organization,
        "organization_limit": org_limit,
        "credential_limit": key_limit,
        "window_seconds": _integer(
            window_seconds,
            "window_seconds",
            MIN_WINDOW_SECONDS,
            MAX_WINDOW_SECONDS,
        ),
        "source": "default",
    }
    return {
        "version": VERSION,
        **body,
        "policy_digest": _digest(body, "default quota policy"),
    }


def _window(policy: Mapping[str, Any], now: Any) -> tuple[int, int, int]:
    timestamp = _timestamp(now, "now")
    window_seconds = int(policy["window_seconds"])
    started_at = int(timestamp // window_seconds) * window_seconds
    reset_at = started_at + window_seconds
    return started_at, reset_at, max(1, reset_at - int(timestamp))


def _decision(
    policy: Mapping[str, Any],
    *,
    api_key_id: str,
    operation: str,
    idempotency_key: str,
    organization_used: int,
    credential_used: int,
    window_started_at: int,
    reset_at: int,
    accepted: bool,
    replayed: bool,
    exceeded_scope: str | None = None,
) -> dict[str, Any]:
    organization_limit = int(policy["organization_limit"])
    credential_limit = int(policy["credential_limit"])
    return {
        "version": VERSION,
        "accepted": accepted,
        "replayed": replayed,
        "operation": operation,
        "idempotency_key": idempotency_key,
        "organization_id": policy["organization_id"],
        "api_key_id": api_key_id,
        "window_started_at": window_started_at,
        "reset_at": reset_at,
        "retry_after_seconds": max(0, reset_at - int(time.time())),
        "exceeded_scope": exceeded_scope,
        "organization": {
            "limit": organization_limit,
            "used": organization_used,
            "remaining": max(0, organization_limit - organization_used),
        },
        "credential": {
            "limit": credential_limit,
            "used": credential_used,
            "remaining": max(0, credential_limit - credential_used),
        },
        "policy_digest": policy["policy_digest"],
    }


class InMemoryQuotaBackend:
    """Thread-safe development adapter matching the Redis quota contract."""

    backend = "memory"
    distributed = False

    def __init__(
        self,
        *,
        organization_limit: Any = DEFAULT_ORGANIZATION_LIMIT,
        credential_limit: Any = DEFAULT_CREDENTIAL_LIMIT,
        window_seconds: Any = DEFAULT_WINDOW_SECONDS,
    ) -> None:
        self.organization_limit = organization_limit
        self.credential_limit = credential_limit
        self.window_seconds = window_seconds
        self._policies: dict[str, dict[str, Any]] = {}
        self._counters: dict[tuple[str, str, int], int] = {}
        self._idempotency: dict[tuple[str, str, str], dict[str, Any]] = {}
        self._lock = threading.RLock()

    def get_policy(self, organization_id: Any) -> dict[str, Any]:
        organization = _organization_id(organization_id)
        with self._lock:
            policy = self._policies.get(organization)
            if policy is None:
                policy = default_quota_policy(
                    organization,
                    organization_limit=self.organization_limit,
                    credential_limit=self.credential_limit,
                    window_seconds=self.window_seconds,
                )
            return deepcopy(policy)

    def set_policy(self, policy: Mapping[str, Any]) -> dict[str, Any]:
        organization = _organization_id(policy.get("organization_id"))
        normalized = normalize_quota_policy(
            organization,
            policy,
            updated_by=policy.get("updated_by"),
            updated_at=policy.get("updated_at"),
        )
        if policy.get("policy_digest") not in {None, normalized["policy_digest"]}:
            raise ValueError("quota policy_digest does not match its content")
        with self._lock:
            self._policies[organization] = normalized
        return deepcopy(normalized)

    def consume(
        self,
        organization_id: Any,
        api_key_id: Any,
        operation: Any,
        idempotency_key: Any,
        payload_digest: Any,
        *,
        now: Any = None,
    ) -> dict[str, Any]:
        organization = _organization_id(organization_id)
        credential = _api_key_id(api_key_id)
        normalized_operation = _operation(operation)
        request_id = normalize_idempotency_key(idempotency_key)
        digest = str(payload_digest or "")
        if not digest.startswith("sha256:") or len(digest) != 71:
            raise ValueError("payload_digest must be a SHA-256 request digest")
        timestamp = _timestamp(time.time() if now is None else now, "now")
        policy = self.get_policy(organization)
        started_at, reset_at, _ = _window(policy, timestamp)
        org_counter = (organization, "organization", started_at)
        key_counter = (organization, credential, started_at)
        idem_key = (organization, credential, request_id)
        with self._lock:
            self._evict(timestamp)
            existing = self._idempotency.get(idem_key)
            if existing is not None:
                if existing["payload_digest"] != digest:
                    raise IdempotencyConflictError("Idempotency-Key was already used for a different request")
                replay = deepcopy(existing["decision"])
                replay["replayed"] = True
                replay["retry_after_seconds"] = max(
                    0,
                    int(replay["reset_at"]) - int(timestamp),
                )
                return replay

            organization_used = self._counters.get(org_counter, 0)
            credential_used = self._counters.get(key_counter, 0)
            exceeded_scope = None
            if organization_used >= policy["organization_limit"]:
                exceeded_scope = "organization"
            elif credential_used >= policy["credential_limit"]:
                exceeded_scope = "credential"
            if exceeded_scope is not None:
                decision = _decision(
                    policy,
                    api_key_id=credential,
                    operation=normalized_operation,
                    idempotency_key=request_id,
                    organization_used=organization_used,
                    credential_used=credential_used,
                    window_started_at=started_at,
                    reset_at=reset_at,
                    accepted=False,
                    replayed=False,
                    exceeded_scope=exceeded_scope,
                )
                decision["retry_after_seconds"] = max(1, reset_at - int(timestamp))
                raise QuotaExceededError(decision)

            organization_used += 1
            credential_used += 1
            self._counters[org_counter] = organization_used
            self._counters[key_counter] = credential_used
            decision = _decision(
                policy,
                api_key_id=credential,
                operation=normalized_operation,
                idempotency_key=request_id,
                organization_used=organization_used,
                credential_used=credential_used,
                window_started_at=started_at,
                reset_at=reset_at,
                accepted=True,
                replayed=False,
            )
            decision["retry_after_seconds"] = max(1, reset_at - int(timestamp))
            self._idempotency[idem_key] = {
                "payload_digest": digest,
                "expires_at": timestamp + IDEMPOTENCY_TTL_SECONDS,
                "decision": deepcopy(decision),
            }
            return decision

    def usage(
        self,
        organization_id: Any,
        *,
        api_key_id: Any = None,
        now: Any = None,
    ) -> dict[str, Any]:
        organization = _organization_id(organization_id)
        credential = None if api_key_id is None else _api_key_id(api_key_id)
        timestamp = _timestamp(time.time() if now is None else now, "now")
        policy = self.get_policy(organization)
        started_at, reset_at, _ = _window(policy, timestamp)
        with self._lock:
            organization_used = self._counters.get(
                (organization, "organization", started_at),
                0,
            )
            credential_used = (
                None if credential is None else self._counters.get((organization, credential, started_at), 0)
            )
        return _usage_snapshot(
            policy,
            organization_used=organization_used,
            credential_used=credential_used,
            api_key_id=credential,
            window_started_at=started_at,
            reset_at=reset_at,
        )

    def _evict(self, now: float) -> None:
        expired_idempotency = [
            key for key, record in self._idempotency.items() if record["expires_at"] <= now
        ]
        for idempotency_key in expired_idempotency:
            del self._idempotency[idempotency_key]
        minimum_window = int(now) - MAX_WINDOW_SECONDS
        expired_counters = [key for key in self._counters if key[2] < minimum_window]
        for counter_key in expired_counters:
            del self._counters[counter_key]


def _usage_snapshot(
    policy: Mapping[str, Any],
    *,
    organization_used: int,
    credential_used: int | None,
    api_key_id: str | None,
    window_started_at: int,
    reset_at: int,
) -> dict[str, Any]:
    organization_limit = int(policy["organization_limit"])
    credential_limit = int(policy["credential_limit"])
    return {
        "version": VERSION,
        "backend": None,
        "organization_id": policy["organization_id"],
        "api_key_id": api_key_id,
        "window_started_at": window_started_at,
        "reset_at": reset_at,
        "organization": {
            "limit": organization_limit,
            "used": organization_used,
            "remaining": max(0, organization_limit - organization_used),
        },
        "credential": (
            None
            if credential_used is None
            else {
                "limit": credential_limit,
                "used": credential_used,
                "remaining": max(0, credential_limit - credential_used),
            }
        ),
        "policy_digest": policy["policy_digest"],
    }


class RedisQuotaBackend:
    """Redis-backed atomic organization and credential quota adapter."""

    backend = "redis"
    distributed = True

    _CONSUME_SCRIPT = """
local existing = redis.call('GET', KEYS[3])
if existing then
  local stored_digest, org_used, credential_used, started_at, reset_at =
    string.match(existing, '^([^|]+)|([^|]+)|([^|]+)|([^|]+)|([^|]+)$')
  if stored_digest ~= ARGV[1] then
    return {-1, 0, 0}
  end
  return {
    2,
    tonumber(org_used),
    tonumber(credential_used),
    tonumber(started_at),
    tonumber(reset_at)
  }
end

local org_used = tonumber(redis.call('GET', KEYS[1]) or '0')
local credential_used = tonumber(redis.call('GET', KEYS[2]) or '0')
if org_used >= tonumber(ARGV[2]) then
  return {0, org_used, credential_used, 1}
end
if credential_used >= tonumber(ARGV[3]) then
  return {0, org_used, credential_used, 2}
end

org_used = redis.call('INCR', KEYS[1])
credential_used = redis.call('INCR', KEYS[2])
if org_used == 1 then redis.call('EXPIRE', KEYS[1], tonumber(ARGV[4])) end
if credential_used == 1 then redis.call('EXPIRE', KEYS[2], tonumber(ARGV[4])) end
redis.call(
  'SET',
  KEYS[3],
  ARGV[1] .. '|' .. org_used .. '|' .. credential_used .. '|' .. ARGV[6] .. '|' .. ARGV[7],
  'EX',
  tonumber(ARGV[5]),
  'NX'
)
return {1, org_used, credential_used}
"""

    def __init__(
        self,
        client: Any,
        *,
        key_prefix: str = "nfl:v44:quota",
        organization_limit: Any = DEFAULT_ORGANIZATION_LIMIT,
        credential_limit: Any = DEFAULT_CREDENTIAL_LIMIT,
        window_seconds: Any = DEFAULT_WINDOW_SECONDS,
    ) -> None:
        self.client = client
        self.key_prefix = str(key_prefix or "").strip().rstrip(":")
        if not self.key_prefix or len(self.key_prefix) > 120:
            raise ValueError("key_prefix must contain 1-120 characters")
        self.organization_limit = organization_limit
        self.credential_limit = credential_limit
        self.window_seconds = window_seconds
        self._policies_key = f"{self.key_prefix}:policies"

    @staticmethod
    def _text(value: Any) -> str:
        return value.decode() if isinstance(value, bytes) else str(value)

    def get_policy(self, organization_id: Any) -> dict[str, Any]:
        organization = _organization_id(organization_id)
        raw = self.client.hget(self._policies_key, organization)
        if raw is None:
            return default_quota_policy(
                organization,
                organization_limit=self.organization_limit,
                credential_limit=self.credential_limit,
                window_seconds=self.window_seconds,
            )
        try:
            return _validated_quota_policy(
                json.loads(self._text(raw)),
                organization,
            )
        except (TypeError, ValueError) as exc:
            raise RuntimeError("enterprise quota policy failed integrity validation") from exc

    def set_policy(self, policy: Mapping[str, Any]) -> dict[str, Any]:
        organization = _organization_id(policy.get("organization_id"))
        normalized = normalize_quota_policy(
            organization,
            policy,
            updated_by=policy.get("updated_by"),
            updated_at=policy.get("updated_at"),
        )
        if policy.get("policy_digest") not in {None, normalized["policy_digest"]}:
            raise ValueError("quota policy_digest does not match its content")
        self.client.hset(
            self._policies_key,
            organization,
            _canonical_json(normalized, "quota policy"),
        )
        return normalized

    def consume(
        self,
        organization_id: Any,
        api_key_id: Any,
        operation: Any,
        idempotency_key: Any,
        payload_digest: Any,
        *,
        now: Any = None,
    ) -> dict[str, Any]:
        organization = _organization_id(organization_id)
        credential = _api_key_id(api_key_id)
        normalized_operation = _operation(operation)
        request_id = normalize_idempotency_key(idempotency_key)
        digest = str(payload_digest or "")
        if not digest.startswith("sha256:") or len(digest) != 71:
            raise ValueError("payload_digest must be a SHA-256 request digest")
        timestamp = _timestamp(time.time() if now is None else now, "now")
        policy = self.get_policy(organization)
        started_at, reset_at, counter_ttl = _window(policy, timestamp)
        identity_digest = hashlib.sha256(f"{organization}:{credential}:{request_id}".encode()).hexdigest()
        org_key = f"{self.key_prefix}:usage:{organization}:{started_at}:organization"
        credential_key = f"{self.key_prefix}:usage:{organization}:{started_at}:{credential}"
        idempotency_storage_key = f"{self.key_prefix}:idempotency:{identity_digest}"
        result = self.client.eval(
            self._CONSUME_SCRIPT,
            3,
            org_key,
            credential_key,
            idempotency_storage_key,
            digest,
            int(policy["organization_limit"]),
            int(policy["credential_limit"]),
            counter_ttl + 1,
            IDEMPOTENCY_TTL_SECONDS,
            started_at,
            reset_at,
        )
        state = int(result[0])
        organization_used = int(result[1])
        credential_used = int(result[2])
        if state == -1:
            raise IdempotencyConflictError("Idempotency-Key was already used for a different request")
        if state == 0:
            scope = "organization" if int(result[3]) == 1 else "credential"
            decision = _decision(
                policy,
                api_key_id=credential,
                operation=normalized_operation,
                idempotency_key=request_id,
                organization_used=organization_used,
                credential_used=credential_used,
                window_started_at=started_at,
                reset_at=reset_at,
                accepted=False,
                replayed=False,
                exceeded_scope=scope,
            )
            decision["retry_after_seconds"] = counter_ttl
            raise QuotaExceededError(decision)
        decision_started_at = int(result[3]) if state == 2 else started_at
        decision_reset_at = int(result[4]) if state == 2 else reset_at
        decision = _decision(
            policy,
            api_key_id=credential,
            operation=normalized_operation,
            idempotency_key=request_id,
            organization_used=organization_used,
            credential_used=credential_used,
            window_started_at=decision_started_at,
            reset_at=decision_reset_at,
            accepted=True,
            replayed=state == 2,
        )
        decision["retry_after_seconds"] = (
            max(0, decision_reset_at - int(timestamp)) if state == 2 else counter_ttl
        )
        return decision

    def usage(
        self,
        organization_id: Any,
        *,
        api_key_id: Any = None,
        now: Any = None,
    ) -> dict[str, Any]:
        organization = _organization_id(organization_id)
        credential = None if api_key_id is None else _api_key_id(api_key_id)
        timestamp = _timestamp(time.time() if now is None else now, "now")
        policy = self.get_policy(organization)
        started_at, reset_at, _ = _window(policy, timestamp)
        org_key = f"{self.key_prefix}:usage:{organization}:{started_at}:organization"
        keys = [org_key]
        if credential is not None:
            keys.append(f"{self.key_prefix}:usage:{organization}:{started_at}:{credential}")
        values = self.client.mget(keys)
        organization_used = int(values[0] or 0)
        credential_used = None if credential is None else int(values[1] or 0)
        snapshot = _usage_snapshot(
            policy,
            organization_used=organization_used,
            credential_used=credential_used,
            api_key_id=credential,
            window_started_at=started_at,
            reset_at=reset_at,
        )
        snapshot["backend"] = self.backend
        return snapshot


def _environment_integer(name: str, default: int) -> int:
    raw = os.getenv(name)
    return default if raw is None else int(raw)


def get_quota_backend() -> InMemoryQuotaBackend | RedisQuotaBackend:
    """Return the application quota backend, failing closed in production."""
    configured = current_app.extensions.get("enterprise_quota_backend")
    if configured is not None:
        return configured

    try:
        organization_limit = _environment_integer(
            "V44_ORGANIZATION_QUOTA",
            DEFAULT_ORGANIZATION_LIMIT,
        )
        credential_limit = _environment_integer(
            "V44_CREDENTIAL_QUOTA",
            DEFAULT_CREDENTIAL_LIMIT,
        )
        window_seconds = _environment_integer(
            "V44_QUOTA_WINDOW_SECONDS",
            DEFAULT_WINDOW_SECONDS,
        )
        default_quota_policy(
            "org_00000000000000000000",
            organization_limit=organization_limit,
            credential_limit=credential_limit,
            window_seconds=window_seconds,
        )
    except ValueError as exc:
        raise RuntimeError("enterprise quota environment configuration is invalid") from exc
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
            backend: InMemoryQuotaBackend | RedisQuotaBackend = RedisQuotaBackend(
                client,
                organization_limit=organization_limit,
                credential_limit=credential_limit,
                window_seconds=window_seconds,
            )
        except Exception as exc:
            raise RuntimeError("enterprise quota Redis backend is unavailable") from exc
    elif os.getenv("APP_ENV", os.getenv("FLASK_ENV", "development")).lower() == "production":
        raise RuntimeError("REDIS_URL is required for production enterprise quotas")
    else:
        backend = InMemoryQuotaBackend(
            organization_limit=organization_limit,
            credential_limit=credential_limit,
            window_seconds=window_seconds,
        )
    current_app.extensions["enterprise_quota_backend"] = backend
    return backend


def quota_manifest() -> dict[str, Any]:
    """Describe the bounded public decision and quota contract."""
    return {
        "version": VERSION,
        "operations": sorted(_OPERATIONS),
        "idempotency_required": True,
        "idempotency_ttl_seconds": IDEMPOTENCY_TTL_SECONDS,
        "limits": {
            "minimum": MIN_QUOTA_LIMIT,
            "maximum": MAX_QUOTA_LIMIT,
            "window_seconds_minimum": MIN_WINDOW_SECONDS,
            "window_seconds_maximum": MAX_WINDOW_SECONDS,
            "request_bytes": MAX_REQUEST_BYTES,
        },
        "production_backend": "redis",
        "development_backend": "memory",
        "fail_closed_without_production_redis": True,
    }
