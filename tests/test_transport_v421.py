import pytest

from distributed_v42 import normalize_job, transition_job
from transport_v421 import (
    InMemoryStreamTransport,
    build_transport,
    normalize_lease,
    recover_stale_job,
    transport_manifest,
)


def _job(**overrides):
    payload = {
        "job_type": "model.evaluate",
        "payload": {"model": "power-v7", "season": 2026},
        "submitted_at": 100.0,
    }
    payload.update(overrides)
    return normalize_job(payload, now=payload["submitted_at"])


def test_manifest_preserves_contract_and_exposes_transport():
    manifest = transport_manifest()
    assert manifest["version"] == "4.2.1"
    assert manifest["job_contract_version"] == "4.2.0"
    assert manifest["features"]["redis_stream_transport"] is True
    assert manifest["features"]["stale_lease_recovery"] is True


def test_lease_is_deterministic_and_bounded():
    running = transition_job(_job(), "running", now=101.0, worker_id="worker-a")
    first = normalize_lease(running, "1-0", "worker-a", claimed_at=101.0)
    second = normalize_lease(running, "1-0", "worker-a", claimed_at=101.0)
    assert first == second
    assert first["expires_at"] == 161.0
    assert first["lease_token"].startswith("lease_")


def test_lease_rejects_mismatched_worker():
    running = transition_job(_job(), "running", now=101.0, worker_id="worker-a")
    with pytest.raises(ValueError, match="match"):
        normalize_lease(running, "1-0", "worker-b", claimed_at=101.0)


def test_memory_transport_enqueues_and_deduplicates():
    transport = InMemoryStreamTransport()
    first = transport.enqueue(_job())
    repeated = transport.enqueue(_job())
    assert first["accepted"] is True
    assert repeated["deduplicated"] is True
    assert repeated["message_id"] == first["message_id"]


def test_memory_transport_detects_identity_conflict():
    transport = InMemoryStreamTransport()
    transport.enqueue(_job(idempotency_key="weekly"))
    with pytest.raises(ValueError, match="conflicts"):
        transport.enqueue(
            _job(idempotency_key="weekly", payload={"model": "different"})
        )


def test_memory_transport_claims_and_acknowledges_terminal_job():
    transport = InMemoryStreamTransport()
    message = transport.enqueue(_job())
    claim = transport.claim("worker-a", now=101.0)[0]
    assert claim["message_id"] == message["message_id"]
    assert claim["job"]["status"] == "running"
    succeeded = transition_job(
        claim["job"],
        "succeeded",
        now=102.0,
        result={"score": 0.91},
    )
    result = transport.acknowledge(
        claim["message_id"],
        "worker-a",
        succeeded,
    )
    assert result["acknowledged"] is True
    assert transport.claim("worker-b", now=103.0) == []


def test_acknowledgement_requires_owner_and_terminal_state():
    transport = InMemoryStreamTransport()
    transport.enqueue(_job())
    claim = transport.claim("worker-a", now=101.0)[0]
    with pytest.raises(ValueError, match="terminal"):
        transport.acknowledge(claim["message_id"], "worker-a", claim["job"])
    failed = transition_job(claim["job"], "failed", now=102.0, error="timeout")
    with pytest.raises(ValueError, match="own"):
        transport.acknowledge(claim["message_id"], "worker-b", failed)


def test_stale_lease_recovery_reclaims_with_next_attempt():
    transport = InMemoryStreamTransport(lease_seconds=10)
    transport.enqueue(_job(max_attempts=3))
    first = transport.claim("worker-a", now=101.0)[0]
    recovered = transport.recover_stale("worker-b", now=111.0)
    assert recovered[0]["action"] == "reclaimed"
    assert recovered[0]["job"]["attempt"] == 2
    assert recovered[0]["job"]["worker_id"] == "worker-b"
    assert recovered[0]["lease"]["message_id"] == first["message_id"]


def test_stale_lease_recovery_exhausts_attempt_limit():
    running = transition_job(
        _job(max_attempts=1),
        "running",
        now=101.0,
        worker_id="worker-a",
    )
    lease = normalize_lease(
        running,
        "1-0",
        "worker-a",
        claimed_at=101.0,
        lease_seconds=10,
    )
    outcome = recover_stale_job(
        running,
        lease,
        "worker-b",
        recovered_at=111.0,
        lease_seconds=10,
    )
    assert outcome["action"] == "exhausted"
    assert outcome["job"]["status"] == "failed"
    assert outcome["lease"] is None


def test_factory_uses_memory_only_when_redis_is_not_configured(monkeypatch):
    monkeypatch.delenv("REDIS_URL", raising=False)
    transport = build_transport(redis_url=None)
    assert transport.backend == "memory"
    with pytest.raises(RuntimeError, match="REDIS_URL"):
        build_transport(redis_url=None, allow_memory_fallback=False)
