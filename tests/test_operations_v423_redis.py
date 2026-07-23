import os
import uuid

import pytest
from redis import Redis

import operations_v423
from distributed_v42 import normalize_job, transition_job
from operations_v423 import RedisDistributedCache
from transport_v421 import RedisStreamTransport


@pytest.fixture
def redis_client():
    redis_url = os.getenv("REDIS_URL")
    if not redis_url:
        pytest.skip("REDIS_URL is not configured")
    return Redis.from_url(redis_url, decode_responses=True)


@pytest.fixture
def cache(redis_client):
    prefix = f"nfl:test:v423:cache:{uuid.uuid4().hex}"
    current = RedisDistributedCache(redis_client, key_prefix=prefix)
    yield current
    keys = list(redis_client.scan_iter(f"{prefix}:*"))
    if keys:
        redis_client.delete(*keys)


@pytest.fixture
def transport(redis_client):
    prefix = f"nfl:test:v423:transport:{uuid.uuid4().hex}"
    current = RedisStreamTransport(
        redis_client,
        key_prefix=prefix,
        lease_seconds=10,
    )
    yield current
    keys = list(redis_client.scan_iter(f"{prefix}:*"))
    if keys:
        redis_client.delete(*keys)


def _job(**overrides):
    source = {
        "job_type": "simulation.run",
        "payload": {"home_win_probability": 0.55, "trials": 100, "seed": 7},
        "submitted_at": 100.0,
    }
    source.update(overrides)
    return normalize_job(source, now=source["submitted_at"])


def test_redis_cache_round_trips_json(cache):
    record = cache.set(
        "projections",
        "player:13",
        {"projection": 72.5},
        ttl_seconds=60,
        tags=["week-1"],
    )
    assert record["storage_key"].startswith(cache.key_prefix)
    assert cache.get("projections", "player:13") == {"projection": 72.5}


def test_redis_cache_invalidates_tags_and_records_event(cache):
    cache.set(
        "projections",
        "player:13",
        {"projection": 72.5},
        tags=["week-1"],
    )
    cache.set(
        "projections",
        "player:14",
        {"projection": 64.0},
        tags=["week-2"],
    )
    outcome = cache.invalidate(
        {
            "namespace": "projections",
            "tags": ["week-1"],
            "occurred_at": 101.0,
            "reason": "new injury report",
        }
    )
    assert outcome["invalidated"] == 1
    assert cache.get("projections", "player:13") is None
    assert cache.get("projections", "player:14") == {"projection": 64.0}
    assert cache.recent_invalidations(1)[0]["reason"] == "new injury report"


def test_redis_cache_bounded_invalidation_makes_forward_progress(cache, monkeypatch):
    monkeypatch.setattr(operations_v423, "MAX_INVALIDATION_KEYS", 2)
    for player_id in ("13", "14", "15"):
        cache.set(
            "projections",
            f"player:{player_id}",
            {"projection": 70},
            tags=["week-1"],
        )
    first = cache.invalidate(
        {
            "namespace": "projections",
            "tags": ["week-1"],
            "occurred_at": 101.0,
            "reason": "new data",
        }
    )
    second = cache.invalidate(
        {
            "namespace": "projections",
            "tags": ["week-1"],
            "occurred_at": 102.0,
            "sequence": 2,
            "reason": "new data",
        }
    )
    assert first["invalidated"] == 2
    assert first["truncated"] is True
    assert second["invalidated"] == 1
    assert second["truncated"] is False


def test_redis_transport_reports_queue_and_latency_metrics(transport):
    transport.enqueue(_job())
    queued = transport.operations_snapshot(now=101.0)
    assert queued["queue_depth"] == 1
    claim = transport.claim("worker-a", now=102.0)[0]
    pending = transport.operations_snapshot(now=102.0)
    assert pending["pending_depth"] == 1
    assert pending["claim_latency_seconds"]["average"] == 2.0
    completed = transition_job(
        claim["job"],
        "succeeded",
        now=104.0,
        result={"ok": True},
    )
    transport.acknowledge(claim["message_id"], "worker-a", completed)
    final = transport.operations_snapshot(now=104.0)
    assert final["queue_depth"] == 0
    assert final["pending_depth"] == 0
    assert final["acknowledged_total"] == 1
    assert final["completion_latency_seconds"]["average"] == 4.0


def test_redis_transport_records_and_lists_dead_letters(transport):
    transport.enqueue(_job())
    claim = transport.claim("worker-a", now=101.0)[0]
    failed = transition_job(
        claim["job"],
        "failed",
        now=102.0,
        error="provider unavailable",
    )
    transport.acknowledge(claim["message_id"], "worker-a", failed)
    records = transport.list_dead_letters(limit=10)
    assert len(records) == 1
    assert records[0]["job_id"] == failed["job_id"]
    assert "payload" not in records[0]
    assert transport.operations_snapshot(now=102.0)["dead_letter_depth"] == 1


def test_redis_component_health_uses_ping(cache, transport):
    assert cache.health()["healthy"] is True
    assert transport.health()["healthy"] is True
    assert cache.health()["durable"] is True
