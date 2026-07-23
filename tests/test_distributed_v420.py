import math

import pytest

from distributed_v42 import (
    InMemoryJobRegistry,
    job_event,
    normalize_job,
    platform_manifest,
    transition_job,
)


def _job(**overrides):
    payload = {
        "job_type": "model.evaluate",
        "payload": {"model": "power-v7", "season": 2026},
        "submitted_at": 100.0,
    }
    payload.update(overrides)
    return normalize_job(payload, now=payload["submitted_at"])


def test_job_normalization_is_deterministic_and_inspectable():
    first = _job()
    second = _job()
    assert first["job_id"] == second["job_id"]
    assert first["payload_digest"] == second["payload_digest"]
    assert first["idempotency_key_source"] == "content"
    assert first["status"] == "queued"


def test_caller_idempotency_key_controls_identity():
    first = _job(idempotency_key="weekly-evaluation")
    second = _job(idempotency_key="weekly-evaluation", payload={"model": "other"})
    assert first["job_id"] == second["job_id"]
    assert first["payload_digest"] != second["payload_digest"]


def test_job_contract_rejects_non_finite_values():
    with pytest.raises(ValueError, match="finite"):
        _job(payload={"probability": math.nan})


def test_running_transition_requires_worker_and_increments_attempt():
    with pytest.raises(ValueError, match="worker_id"):
        transition_job(_job(), "running", now=101.0)
    running = transition_job(_job(), "running", now=101.0, worker_id="worker-a")
    assert running["attempt"] == 1
    assert running["worker_id"] == "worker-a"
    assert running["started_at"] == 101.0


def test_success_transition_preserves_bounded_result():
    running = transition_job(_job(), "running", now=101.0, worker_id="worker-a")
    succeeded = transition_job(running, "succeeded", now=102.0, result={"score": 0.91})
    assert succeeded["status"] == "succeeded"
    assert succeeded["result"] == {"score": 0.91}
    assert succeeded["completed_at"] == 102.0


def test_invalid_terminal_transition_is_rejected():
    cancelled = transition_job(_job(), "cancelled", now=101.0)
    with pytest.raises(ValueError, match="cannot transition"):
        transition_job(cancelled, "running", now=102.0, worker_id="worker-a")


def test_failed_job_can_retry_until_attempt_limit():
    job = _job(max_attempts=1)
    running = transition_job(job, "running", now=101.0, worker_id="worker-a")
    failed = transition_job(running, "failed", now=102.0, error="provider timeout")
    with pytest.raises(ValueError, match="exhausted"):
        transition_job(failed, "queued", now=103.0)


def test_event_envelopes_are_deterministic_and_ordered():
    job = _job()
    first = job_event(job, "job.queued", 1, occurred_at=100.0)
    repeated = job_event(job, "job.queued", 1, occurred_at=100.0)
    assert first == repeated
    assert first["event_id"].startswith("evt_")
    assert first["sequence"] == 1


def test_registry_deduplicates_and_detects_conflicts():
    registry = InMemoryJobRegistry()
    payload = {
        "job_type": "model.evaluate",
        "idempotency_key": "weekly",
        "payload": {"model": "v1"},
    }
    assert registry.submit(payload, now=100.0)["accepted"] is True
    assert registry.submit(payload, now=101.0)["deduplicated"] is True
    with pytest.raises(ValueError, match="conflicts"):
        registry.submit({**payload, "payload": {"model": "v2"}}, now=102.0)


def test_manifest_discloses_current_transport_boundary():
    manifest = platform_manifest()
    assert manifest["version"] == "4.2.0"
    assert manifest["features"]["idempotent_job_contracts"] is True
    assert manifest["features"]["redis_stream_transport"] is False
    assert "Redis Streams" in manifest["next_increment"]
