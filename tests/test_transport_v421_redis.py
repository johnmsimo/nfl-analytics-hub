import os
import uuid

import pytest
from redis import Redis

from distributed_v42 import normalize_job, transition_job
from transport_v421 import RedisStreamTransport


@pytest.fixture
def transport():
    redis_url = os.getenv("REDIS_URL")
    if not redis_url:
        pytest.skip("REDIS_URL is not configured")
    client = Redis.from_url(redis_url, decode_responses=True)
    prefix = f"nfl:test:v421:{uuid.uuid4().hex}"
    current = RedisStreamTransport(
        client,
        key_prefix=prefix,
        lease_seconds=10,
    )
    yield current
    client.delete(
        current.stream_key,
        current.jobs_key,
        current.digests_key,
        current.messages_key,
        current.leases_key,
        current.dead_letters_key,
        current.claim_latencies_key,
        current.completion_latencies_key,
        current.operations_metrics_key,
    )


def _job(**overrides):
    payload = {
        "job_type": "model.evaluate",
        "payload": {"model": "power-v7"},
        "submitted_at": 100.0,
    }
    payload.update(overrides)
    return normalize_job(payload, now=payload["submitted_at"])


def test_redis_transport_enqueues_claims_and_acknowledges(transport):
    submitted = transport.enqueue(_job())
    repeated = transport.enqueue(_job())
    assert submitted["accepted"] is True
    assert repeated["deduplicated"] is True
    claim = transport.claim("worker-a", now=101.0)[0]
    succeeded = transition_job(
        claim["job"],
        "succeeded",
        now=102.0,
        result={"score": 0.91},
    )
    result = transport.acknowledge(claim["message_id"], "worker-a", succeeded)
    assert result["acknowledged"] is True
    assert transport.claim("worker-b", now=103.0) == []


def test_redis_transport_rejects_conflicting_job_identity(transport):
    transport.enqueue(_job(idempotency_key="weekly"))
    with pytest.raises(ValueError, match="conflicts"):
        transport.enqueue(
            _job(idempotency_key="weekly", payload={"model": "different"})
        )


def test_redis_transport_recovers_pending_message(transport):
    transport.enqueue(_job(max_attempts=3))
    claim = transport.claim("worker-a", now=101.0)[0]
    recovered = transport.recover_stale(
        "worker-b",
        now=111.0,
        min_idle_ms=0,
    )
    assert recovered[0]["message_id"] == claim["message_id"]
    assert recovered[0]["action"] == "reclaimed"
    assert recovered[0]["job"]["attempt"] == 2


def test_redis_transport_recovers_claim_crash_before_lease_write(transport):
    submitted = transport.enqueue(_job(max_attempts=3))
    batches = transport.client.xreadgroup(
        transport.group,
        "crashed-worker",
        {transport.stream_key: ">"},
        count=1,
    )
    assert batches[0][1][0][0] == submitted["message_id"]
    recovered = transport.recover_stale(
        "worker-b",
        now=101.0,
        min_idle_ms=0,
    )
    assert recovered[0]["action"] == "reclaimed"
    assert recovered[0]["job"]["attempt"] == 1
    assert recovered[0]["job"]["worker_id"] == "worker-b"
