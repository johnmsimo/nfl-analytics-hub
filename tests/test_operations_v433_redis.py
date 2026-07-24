import os

import pytest
from redis import Redis

from operations_v433 import RedisLifecycleOperations

_DIGEST = "a" * 64
_EVIDENCE = "e" * 64


@pytest.fixture()
def store():
    redis_url = os.getenv("REDIS_URL")
    if not redis_url:
        pytest.skip("REDIS_URL is not configured")
    client = Redis.from_url(redis_url, decode_responses=True)
    prefix = f"test:nfl:v433:{os.getpid()}"
    current = RedisLifecycleOperations(client, key_prefix=prefix)
    keys = list(client.scan_iter(f"{prefix}:*"))
    if keys:
        client.delete(*keys)
    yield current
    keys = list(client.scan_iter(f"{prefix}:*"))
    if keys:
        client.delete(*keys)


def _model():
    return {
        "model_key": "win-probability",
        "version": "v8",
        "target": "home-win",
        "algorithm": "ensemble",
        "feature_schema": [],
        "artifact": {"uri": "s3://models/v8.bin", "digest": _DIGEST},
        "registered_by": "trainer",
    }


def test_redis_registry_round_trips_and_deduplicates(store):
    first = store.register(_model(), registered_at=100.0)
    second = store.register(_model(), registered_at=200.0)
    assert first["accepted"] is True
    assert second["deduplicated"] is True
    assert store.get(first["model_version"]["model_version_id"])["registered_at"] == 100.0


def test_redis_approval_controls_transition_and_audit(store):
    registered = store.register(_model(), registered_at=100.0)["model_version"]
    candidate = store.transition(
        registered["model_version_id"],
        "candidate",
        occurred_at=101.0,
        actor="trainer",
        reason="evaluate",
    )
    approval = store.request_approval(
        {
            "action": "model.lifecycle.transition",
            "target_status": "champion",
            "resource_id": candidate["model_version_id"],
            "evidence_digest": _EVIDENCE,
            "requested_by": "release-manager",
            "reason": "promote",
            "expires_at": 200.0,
        },
        requested_at=102.0,
    )["approval"]
    approved = store.decide_approval(
        approval["approval_id"],
        "approved",
        decided_by="risk-owner",
        reason="verified",
        decided_at=103.0,
    )
    promoted = store.transition(
        candidate["model_version_id"],
        "champion",
        occurred_at=104.0,
        actor="release-manager",
        reason="approved",
        approval_id=approved["approval_id"],
        promotion_decision={
            "policy_id": "policy-main",
            "evaluation_id": "eval-main",
            "evidence_digest": _EVIDENCE,
            "passed": True,
            "evaluated_at": 102.0,
        },
    )
    assert promoted["status"] == "champion"
    assert len(store.audit_history(resource_id=promoted["model_version_id"])) == 5


def test_redis_health_and_status_are_persistent(store):
    model = store.register(_model(), registered_at=100.0)["model_version"]
    store.record_health(
        {
            "model_version_id": model["model_version_id"],
            "checks": [
                {
                    "name": "quality",
                    "healthy": False,
                    "severity": "critical",
                    "observed_at": 101.0,
                    "evidence_digest": _EVIDENCE,
                    "detail": "breach",
                }
            ],
        },
        observed_at=101.0,
        actor="observer",
    )
    snapshot = store.operations_snapshot()
    assert snapshot["backend"] == "redis"
    assert snapshot["durable"] is True
    assert snapshot["health"]["unhealthy_models"] == 1
