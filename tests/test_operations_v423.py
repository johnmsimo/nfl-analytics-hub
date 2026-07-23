import pytest

from distributed_v42 import normalize_job, transition_job
from operations_v423 import (
    InMemoryDistributedCache,
    build_distributed_cache,
    component_health,
    normalize_cache_key,
    normalize_cache_record,
    normalize_invalidation_event,
    operations_manifest,
)
from transport_v421 import InMemoryStreamTransport


def _job(**overrides):
    source = {
        "job_type": "simulation.run",
        "payload": {"home_win_probability": 0.55, "trials": 100, "seed": 7},
        "submitted_at": 100.0,
    }
    source.update(overrides)
    return normalize_job(source, now=source["submitted_at"])


def test_manifest_completes_v42_operations_scope():
    manifest = operations_manifest()
    assert manifest["version"] == "4.2.3"
    assert manifest["job_contract_version"] == "4.2.0"
    assert manifest["features"]["namespaced_distributed_cache"] is True
    assert manifest["features"]["dead_letter_inspection"] is True
    assert manifest["next_increment"] is None


def test_cache_key_is_deterministic_namespaced_and_versioned():
    first = normalize_cache_key("projections", "player:123", cache_version=2)
    second = normalize_cache_key("projections", "player:123", cache_version=2)
    assert first == second
    assert first["storage_key"].startswith("nfl:v42:cache:projections:v2:")
    assert "player:123" not in first["storage_key"]


def test_cache_record_is_bounded_and_json_safe():
    record = normalize_cache_record(
        "scouting",
        "report:13",
        {"matches": [1, 2]},
        ttl_seconds=60,
        tags=["player", "week-1"],
        created_at=100.0,
    )
    assert record["expires_at"] == 160.0
    assert record["tags"] == ["player", "week-1"]
    with pytest.raises(ValueError, match="JSON-safe"):
        normalize_cache_record("scouting", "bad", {"value": float("nan")})


def test_invalidation_event_is_deterministic_and_requires_selector():
    payload = {
        "namespace": "scouting",
        "cache_version": 2,
        "keys": ["report:13"],
        "reason": "roster changed",
    }
    first = normalize_invalidation_event(payload, occurred_at=101.0, sequence=3)
    second = normalize_invalidation_event(payload, occurred_at=101.0, sequence=3)
    assert first == second
    assert first["event_id"].startswith("cache_evt_")
    assert first["cache_version"] == 2
    with pytest.raises(ValueError, match="requires"):
        normalize_invalidation_event({"namespace": "scouting"}, occurred_at=101.0)


def test_memory_cache_sets_reads_and_expires():
    cache = InMemoryDistributedCache()
    cache.set("projections", "player:13", {"value": 72.5}, created_at=100.0)
    assert cache.get("projections", "player:13", now=101.0) == {"value": 72.5}
    assert cache.get("projections", "player:13", now=1_001.0) is None


def test_memory_cache_invalidates_by_tag_without_crossing_namespace():
    cache = InMemoryDistributedCache()
    cache.set(
        "projections",
        "player:13",
        {"value": 72.5},
        tags=["week-1"],
        created_at=100.0,
    )
    cache.set(
        "scouting",
        "player:13",
        {"matches": []},
        tags=["week-1"],
        created_at=100.0,
    )
    outcome = cache.invalidate(
        {
            "namespace": "projections",
            "tags": ["week-1"],
            "occurred_at": 101.0,
            "sequence": 1,
            "reason": "new data",
        }
    )
    assert outcome["invalidated"] == 1
    assert cache.get("projections", "player:13", now=102.0) is None
    assert cache.get("scouting", "player:13", now=102.0) == {"matches": []}


def test_memory_cache_versioned_key_invalidation_is_precise():
    cache = InMemoryDistributedCache()
    cache.set(
        "projections",
        "player:13",
        {"version": 1},
        cache_version=1,
        created_at=100.0,
    )
    cache.set(
        "projections",
        "player:13",
        {"version": 2},
        cache_version=2,
        created_at=100.0,
    )
    cache.invalidate(
        {
            "namespace": "projections",
            "cache_version": 2,
            "keys": ["player:13"],
            "occurred_at": 101.0,
            "reason": "schema change",
        }
    )
    assert cache.get("projections", "player:13", cache_version=1, now=102.0)
    assert (
        cache.get(
            "projections",
            "player:13",
            cache_version=2,
            now=102.0,
        )
        is None
    )


def test_memory_cache_preserves_recent_invalidation_events():
    cache = InMemoryDistributedCache()
    event = {
        "namespace": "rankings",
        "invalidate_namespace": True,
        "occurred_at": 101.0,
        "reason": "weekly refresh",
    }
    cache.invalidate(event)
    assert cache.recent_invalidations(1)[0]["reason"] == "weekly refresh"


def test_cache_factory_falls_back_only_when_redis_is_unconfigured(monkeypatch):
    monkeypatch.delenv("REDIS_URL", raising=False)
    assert build_distributed_cache(redis_url=None).backend == "memory"
    with pytest.raises(RuntimeError, match="REDIS_URL"):
        build_distributed_cache(
            redis_url=None,
            allow_memory_fallback=False,
        )


def test_component_health_marks_memory_as_non_durable():
    health = component_health(InMemoryDistributedCache(), "distributed_cache")
    assert health["healthy"] is True
    assert health["durable"] is False


def test_memory_transport_reports_depth_and_latency():
    transport = InMemoryStreamTransport()
    transport.enqueue(_job())
    queued = transport.operations_snapshot(now=101.0)
    assert queued["queue_depth"] == 1
    assert queued["oldest_queued_age_seconds"] == 1.0
    claim = transport.claim("worker-a", now=102.0)[0]
    running = transport.operations_snapshot(now=102.0)
    assert running["pending_depth"] == 1
    assert running["claim_latency_seconds"]["average"] == 2.0
    completed = transition_job(
        claim["job"],
        "succeeded",
        now=104.0,
        result={"ok": True},
    )
    transport.acknowledge(claim["message_id"], "worker-a", completed)
    final = transport.operations_snapshot(now=104.0)
    assert final["acknowledged_total"] == 1
    assert final["completion_latency_seconds"]["average"] == 4.0


def test_failed_acknowledgement_creates_payload_safe_dead_letter():
    transport = InMemoryStreamTransport()
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
    assert records[0]["error"] == "provider unavailable"
    assert records[0]["payload_digest"] == failed["payload_digest"]
    assert "payload" not in records[0]


def test_exhausted_stale_lease_creates_dead_letter():
    transport = InMemoryStreamTransport(lease_seconds=10)
    transport.enqueue(_job(max_attempts=1))
    transport.claim("worker-a", now=101.0)
    recovered = transport.recover_stale("worker-b", now=111.0)
    assert recovered[0]["action"] == "exhausted"
    assert transport.operations_snapshot(now=111.0)["dead_letter_depth"] == 1
