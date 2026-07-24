import json
import os
import uuid

import pytest

from enterprise_quota_v442 import (
    IdempotencyConflictError,
    QuotaExceededError,
    RedisQuotaBackend,
    normalize_quota_policy,
    request_digest,
)

ORGANIZATION_ID = "org_0123456789abcdef0123"
API_KEY_ONE = "apikey_0123456789abcdef0123"
API_KEY_TWO = "apikey_abcdef0123456789abcd"
ACTOR = {"type": "user", "id": "owner@example.com"}
NOW = 1_800_000_010


@pytest.fixture()
def redis_backend():
    url = os.getenv("REDIS_URL")
    if not url:
        pytest.skip("REDIS_URL is not configured")
    import redis

    client = redis.Redis.from_url(url, decode_responses=True)
    try:
        client.ping()
    except Exception:
        pytest.skip("Redis is not available")
    prefix = f"test:nfl:v442:{uuid.uuid4().hex}"
    backend = RedisQuotaBackend(
        client,
        key_prefix=prefix,
        organization_limit=2,
        credential_limit=1,
        window_seconds=60,
    )
    yield backend
    for key in client.scan_iter(f"{prefix}:*"):
        client.delete(key)


def _consume(backend, key, request_id, payload=None):
    body = payload or {"models": [{"probability": 0.6}]}
    return backend.consume(
        ORGANIZATION_ID,
        key,
        "decision.ensemble",
        request_id,
        request_digest("decision.ensemble", body),
        now=NOW,
    )


def test_redis_meter_is_atomic_and_idempotent(redis_backend):
    first = _consume(redis_backend, API_KEY_ONE, "request-0001")
    replay = _consume(redis_backend, API_KEY_ONE, "request-0001")
    assert first["organization"]["used"] == 1
    assert replay["replayed"] is True
    with pytest.raises(IdempotencyConflictError):
        _consume(
            redis_backend,
            API_KEY_ONE,
            "request-0001",
            {"models": [{"probability": 0.9}]},
        )


def test_redis_replay_preserves_original_window(redis_backend):
    first = _consume(redis_backend, API_KEY_ONE, "request-0001")
    replay = redis_backend.consume(
        ORGANIZATION_ID,
        API_KEY_ONE,
        "decision.ensemble",
        "request-0001",
        request_digest(
            "decision.ensemble",
            {"models": [{"probability": 0.6}]},
        ),
        now=NOW + 60,
    )
    assert replay["replayed"] is True
    assert replay["window_started_at"] == first["window_started_at"]
    assert replay["reset_at"] == first["reset_at"]
    assert replay["retry_after_seconds"] == 0


def test_redis_meter_enforces_credential_and_organization_limits(redis_backend):
    _consume(redis_backend, API_KEY_ONE, "request-0001")
    with pytest.raises(QuotaExceededError) as credential:
        _consume(redis_backend, API_KEY_ONE, "request-0002")
    assert credential.value.decision["exceeded_scope"] == "credential"
    _consume(redis_backend, API_KEY_TWO, "request-0003")
    with pytest.raises(QuotaExceededError) as organization:
        _consume(redis_backend, API_KEY_TWO, "request-0004")
    assert organization.value.decision["exceeded_scope"] == "organization"


def test_redis_policy_and_usage_are_inspectable(redis_backend):
    policy = normalize_quota_policy(
        ORGANIZATION_ID,
        {
            "organization_limit": 20,
            "credential_limit": 10,
            "window_seconds": 300,
        },
        updated_by=ACTOR,
        updated_at=NOW,
    )
    redis_backend.set_policy(policy)
    _consume(redis_backend, API_KEY_ONE, "request-0001")
    usage = redis_backend.usage(
        ORGANIZATION_ID,
        api_key_id=API_KEY_ONE,
        now=NOW,
    )
    assert usage["backend"] == "redis"
    assert usage["organization"]["used"] == 1
    assert usage["credential"]["used"] == 1


def test_redis_policy_tampering_fails_integrity_validation(redis_backend):
    policy = normalize_quota_policy(
        ORGANIZATION_ID,
        {
            "organization_limit": 20,
            "credential_limit": 10,
            "window_seconds": 300,
        },
        updated_by=ACTOR,
        updated_at=NOW,
    )
    redis_backend.set_policy(policy)
    policy["credential_limit"] = 19
    redis_backend.client.hset(
        redis_backend._policies_key,
        ORGANIZATION_ID,
        json.dumps(policy),
    )
    with pytest.raises(RuntimeError, match="integrity validation"):
        redis_backend.get_policy(ORGANIZATION_ID)
