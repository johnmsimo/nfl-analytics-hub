import os
import uuid

import pytest
from redis import Redis

from distributed_v42 import normalize_job, transition_job
from execution_v422 import RedisExecutionStore


@pytest.fixture
def store():
    redis_url = os.getenv("REDIS_URL")
    if not redis_url:
        pytest.skip("REDIS_URL is not configured")
    client = Redis.from_url(redis_url, decode_responses=True)
    prefix = f"nfl:test:v422:{uuid.uuid4().hex}"
    current = RedisExecutionStore(
        client,
        key_prefix=prefix,
        result_ttl_seconds=60,
    )
    yield current
    client.delete(current.results_key, current.cancellations_key)


def _terminal_job():
    queued = normalize_job(
        {
            "job_type": "simulation.run",
            "payload": {
                "home_win_probability": 0.55,
                "trials": 1_000,
                "seed": 7,
            },
            "submitted_at": 100.0,
        },
        now=100.0,
    )
    running = transition_job(
        queued,
        "running",
        now=101.0,
        worker_id="worker-a",
    )
    return transition_job(
        running,
        "succeeded",
        now=102.0,
        result={"home_wins": 550, "trials": 1_000},
    )


def test_redis_store_persists_and_reads_result(store):
    job = _terminal_job()
    outcome = store.persist(job)
    assert outcome["created"] is True
    assert store.get(job["job_id"])["result"]["home_wins"] == 550


def test_redis_store_is_idempotent_for_same_attempt(store):
    job = _terminal_job()
    assert store.persist(job)["created"] is True
    assert store.persist(job)["created"] is False


def test_redis_store_tracks_and_clears_cancellation(store):
    job = _terminal_job()
    request = store.request_cancellation(
        job["job_id"],
        requested_at=101.0,
        reason="operator request",
    )
    assert request["job_id"] == job["job_id"]
    assert store.is_cancelled(job["job_id"]) is True
    assert store.clear_cancellation(job["job_id"]) is True
    assert store.is_cancelled(job["job_id"]) is False
