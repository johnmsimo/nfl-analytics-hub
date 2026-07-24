import math

import pytest
from flask import Flask

from enterprise_quota_v442 import (
    IdempotencyConflictError,
    InMemoryQuotaBackend,
    QuotaExceededError,
    get_quota_backend,
    normalize_idempotency_key,
    normalize_quota_policy,
    quota_manifest,
    request_digest,
)

ORGANIZATION_ID = "org_0123456789abcdef0123"
API_KEY_ONE = "apikey_0123456789abcdef0123"
API_KEY_TWO = "apikey_abcdef0123456789abcd"
ACTOR = {"type": "user", "id": "owner@example.com"}
NOW = 1_800_000_010


def _digest(operation="decision.ensemble", payload=None):
    return request_digest(
        operation,
        payload or {"models": [{"name": "one", "probability": 0.6}]},
    )


def _consume(backend, key=API_KEY_ONE, request_id="request-0001", *, now=NOW):
    return backend.consume(
        ORGANIZATION_ID,
        key,
        "decision.ensemble",
        request_id,
        _digest(),
        now=now,
    )


def test_quota_policy_is_bounded_and_fingerprinted():
    policy = normalize_quota_policy(
        ORGANIZATION_ID,
        {
            "organization_limit": 50,
            "credential_limit": 10,
            "window_seconds": 300,
        },
        updated_by=ACTOR,
        updated_at=NOW,
    )
    assert policy["version"] == "4.4.2"
    assert policy["policy_digest"].startswith("sha256:")
    assert policy["credential_limit"] == 10
    assert policy["updated_by"] == ACTOR


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (
            {
                "organization_limit": 0,
                "credential_limit": 1,
                "window_seconds": 60,
            },
            "organization_limit",
        ),
        (
            {
                "organization_limit": 10,
                "credential_limit": 11,
                "window_seconds": 60,
            },
            "cannot exceed",
        ),
        (
            {
                "organization_limit": 10,
                "credential_limit": 5,
                "window_seconds": 59,
            },
            "window_seconds",
        ),
    ],
)
def test_quota_policy_rejects_unsafe_limits(payload, message):
    with pytest.raises(ValueError, match=message):
        normalize_quota_policy(
            ORGANIZATION_ID,
            payload,
            updated_by=ACTOR,
            updated_at=NOW,
        )


def test_request_digest_binds_operation_and_exact_payload():
    first = _digest()
    assert first == _digest()
    assert first != _digest(payload={"models": [{"name": "one", "probability": 0.7}]})
    assert first != request_digest("decision.scenario", {"baseline": {"probability": 0.6}})
    with pytest.raises(ValueError, match="finite"):
        request_digest("decision.ensemble", {"probability": math.nan})


def test_idempotency_key_contract_is_bounded():
    assert normalize_idempotency_key("request-0001") == "request-0001"
    with pytest.raises(ValueError, match="8-128"):
        normalize_idempotency_key("short")
    with pytest.raises(ValueError, match="8-128"):
        normalize_idempotency_key("unsafe key")


def test_memory_meter_accepts_once_and_replays_without_charging():
    backend = InMemoryQuotaBackend(
        organization_limit=3,
        credential_limit=2,
        window_seconds=60,
    )
    first = _consume(backend)
    replay = _consume(backend)
    usage = backend.usage(
        ORGANIZATION_ID,
        api_key_id=API_KEY_ONE,
        now=NOW,
    )
    assert first["accepted"] is True
    assert first["replayed"] is False
    assert replay["accepted"] is True
    assert replay["replayed"] is True
    assert usage["organization"]["used"] == 1
    assert usage["credential"]["used"] == 1


def test_memory_meter_rejects_idempotency_payload_conflict():
    backend = InMemoryQuotaBackend()
    _consume(backend)
    with pytest.raises(IdempotencyConflictError, match="different request"):
        backend.consume(
            ORGANIZATION_ID,
            API_KEY_ONE,
            "decision.ensemble",
            "request-0001",
            _digest(payload={"models": [{"probability": 0.1}]}),
            now=NOW,
        )


def test_credential_quota_is_independent_but_bounded_by_organization():
    backend = InMemoryQuotaBackend(
        organization_limit=3,
        credential_limit=1,
        window_seconds=60,
    )
    _consume(backend, API_KEY_ONE, "request-0001")
    with pytest.raises(QuotaExceededError) as blocked:
        _consume(backend, API_KEY_ONE, "request-0002")
    assert blocked.value.decision["exceeded_scope"] == "credential"
    second = _consume(backend, API_KEY_TWO, "request-0003")
    assert second["organization"]["used"] == 2
    assert second["credential"]["used"] == 1


def test_organization_quota_aggregates_credentials():
    backend = InMemoryQuotaBackend(
        organization_limit=2,
        credential_limit=2,
        window_seconds=60,
    )
    _consume(backend, API_KEY_ONE, "request-0001")
    _consume(backend, API_KEY_TWO, "request-0002")
    with pytest.raises(QuotaExceededError) as blocked:
        _consume(backend, API_KEY_ONE, "request-0003")
    assert blocked.value.decision["exceeded_scope"] == "organization"
    assert blocked.value.decision["organization"]["remaining"] == 0


def test_fixed_window_resets_counters_but_preserves_usage_metadata():
    backend = InMemoryQuotaBackend(
        organization_limit=1,
        credential_limit=1,
        window_seconds=60,
    )
    first = _consume(backend, request_id="request-0001", now=NOW)
    second = _consume(backend, request_id="request-0002", now=NOW + 60)
    assert second["window_started_at"] == first["window_started_at"] + 60
    assert second["organization"]["used"] == 1


def test_idempotent_replay_preserves_original_window_after_reset():
    backend = InMemoryQuotaBackend(
        organization_limit=2,
        credential_limit=2,
        window_seconds=60,
    )
    first = _consume(backend, request_id="request-0001", now=NOW)
    replay = _consume(backend, request_id="request-0001", now=NOW + 60)
    assert replay["replayed"] is True
    assert replay["window_started_at"] == first["window_started_at"]
    assert replay["reset_at"] == first["reset_at"]
    assert replay["retry_after_seconds"] == 0
    usage = backend.usage(
        ORGANIZATION_ID,
        api_key_id=API_KEY_ONE,
        now=NOW + 60,
    )
    assert usage["organization"]["used"] == 0


def test_policy_override_changes_subsequent_metering():
    backend = InMemoryQuotaBackend()
    policy = normalize_quota_policy(
        ORGANIZATION_ID,
        {
            "organization_limit": 2,
            "credential_limit": 1,
            "window_seconds": 120,
        },
        updated_by=ACTOR,
        updated_at=NOW,
    )
    stored = backend.set_policy(policy)
    assert backend.get_policy(ORGANIZATION_ID) == stored
    _consume(backend, request_id="request-0001")
    with pytest.raises(QuotaExceededError):
        _consume(backend, request_id="request-0002")


def test_production_factory_requires_redis(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv("REDIS_URL", raising=False)
    app = Flask(__name__)
    with app.app_context(), pytest.raises(RuntimeError, match="REDIS_URL"):
        get_quota_backend()


def test_factory_rejects_invalid_environment_as_service_configuration(monkeypatch):
    monkeypatch.setenv("V44_ORGANIZATION_QUOTA", "not-an-integer")
    app = Flask(__name__)
    with app.app_context(), pytest.raises(RuntimeError, match="configuration is invalid"):
        get_quota_backend()


def test_manifest_is_explicit_about_backends_and_operations():
    manifest = quota_manifest()
    assert manifest["production_backend"] == "redis"
    assert manifest["development_backend"] == "memory"
    assert manifest["idempotency_required"] is True
    assert manifest["operations"] == [
        "decision.brief",
        "decision.ensemble",
        "decision.scenario",
    ]
