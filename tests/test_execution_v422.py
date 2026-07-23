from distributed_v42 import normalize_job
from execution_v422 import (
    ExecutionContext,
    ExecutionTimedOut,
    HandlerSpec,
    InMemoryExecutionStore,
    TypedHandlerRegistry,
    TypedWorker,
    build_execution_store,
    execution_manifest,
    normalize_cancellation_request,
)
from transport_v421 import InMemoryStreamTransport


def _job(job_type="simulation.run", payload=None, **overrides):
    source = {
        "job_type": job_type,
        "payload": payload
        or {
            "home_win_probability": 0.6,
            "trials": 1_000,
            "seed": 7,
        },
        "submitted_at": 100.0,
    }
    source.update(overrides)
    return normalize_job(source, now=source["submitted_at"])


def test_manifest_exposes_five_typed_families():
    manifest = execution_manifest()
    assert manifest["version"] == "4.2.2"
    assert manifest["job_contract_version"] == "4.2.0"
    assert {item["family"] for item in manifest["handlers"]} == {
        "model",
        "simulation",
        "scouting",
        "backfill",
        "report",
    }


def test_registry_rejects_unknown_job_types():
    registry = TypedHandlerRegistry()
    try:
        registry.validate(_job(job_type="shell.run"))
    except ValueError as exc:
        assert "no registered" in str(exc)
    else:
        raise AssertionError("unsupported handler was accepted")


def test_simulation_payload_is_typed_and_timeout_is_bounded():
    registry = TypedHandlerRegistry()
    validated = registry.validate(
        _job(
            payload={
                "home_win_probability": 0.55,
                "trials": 500,
                "seed": 3,
                "timeout_seconds": 10,
            }
        )
    )
    assert validated["family"] == "simulation"
    assert validated["timeout_seconds"] == 10
    assert validated["payload"]["trials"] == 500


def test_registry_validates_all_five_handler_payloads():
    registry = TypedHandlerRegistry()
    sources = [
        _job(
            "model.project",
            {
                "rows": [
                    {"receiving_yards": 40},
                    {"receiving_yards": 60},
                    {"receiving_yards": 50},
                ],
                "market": "rec_yds",
                "position": "WR",
            },
            idempotency_key="model",
        ),
        _job(
            "simulation.run",
            {"home_win_probability": 0.55, "trials": 100, "seed": 1},
            idempotency_key="simulation",
        ),
        _job(
            "scouting.analyze",
            {
                "operation": "personnel_tendencies",
                "plays": [{"personnel": "11", "yards": 5}],
            },
            idempotency_key="scouting",
        ),
        _job(
            "backfill.run",
            {
                "start_season": 2024,
                "end_season": 2025,
                "datasets": ["pbp"],
            },
            idempotency_key="backfill",
        ),
        _job(
            "report.generate",
            {
                "report": {
                    "type": "player_comparison",
                    "title": "Comparison",
                    "source_endpoint": "/api/v4.1/scouting/players/similarity",
                    "result": {"matches": []},
                }
            },
            idempotency_key="report",
        ),
    ]
    assert [registry.validate(job)["family"] for job in sources] == [
        "model",
        "simulation",
        "scouting",
        "backfill",
        "report",
    ]


def test_simulation_is_deterministic():
    registry = TypedHandlerRegistry()
    job = _job()
    context = ExecutionContext(job["job_id"], 10.0, lambda _job_id: False, lambda: 0.0)
    first = registry.execute(job, context)
    second = registry.execute(job, context)
    assert first == second
    assert first["home_wins"] + first["away_wins"] == 1_000


def test_context_detects_timeout():
    context = ExecutionContext("job_test", 5.0, lambda _job_id: False, lambda: 5.0)
    try:
        context.checkpoint()
    except ExecutionTimedOut:
        pass
    else:
        raise AssertionError("expired context did not time out")


def test_cancellation_request_is_deterministic():
    first = normalize_cancellation_request(
        "job_123",
        requested_at=101.0,
        reason="operator request",
    )
    second = normalize_cancellation_request(
        "job_123",
        requested_at=101.0,
        reason="operator request",
    )
    assert first == second
    assert first["cancellation_id"].startswith("cancel_")


def test_memory_store_persists_idempotently():
    transport = InMemoryStreamTransport()
    queued = _job()
    transport.enqueue(queued)
    running = transport.claim("worker-a", now=101.0)[0]["job"]
    from distributed_v42 import transition_job

    completed = transition_job(
        running,
        "succeeded",
        now=102.0,
        result={"ok": True},
    )
    store = InMemoryExecutionStore()
    assert store.persist(completed)["created"] is True
    assert store.persist(completed)["created"] is False
    assert store.get(completed["job_id"])["result"] == {"ok": True}


def test_worker_executes_persists_and_acknowledges():
    transport = InMemoryStreamTransport()
    transport.enqueue(_job())
    store = InMemoryExecutionStore()
    wall_times = iter([102.0])
    worker = TypedWorker(
        transport,
        store,
        "worker-a",
        monotonic_clock=lambda: 0.0,
        wall_clock=lambda: next(wall_times),
    )
    outcome = worker.run_once()[0]
    assert outcome["job"]["status"] == "succeeded"
    assert outcome["result_persisted"] is True
    assert outcome["acknowledged"] is True
    assert store.get(outcome["job"]["job_id"])["status"] == "succeeded"


def test_worker_honors_preexisting_cancellation():
    transport = InMemoryStreamTransport()
    submitted = transport.enqueue(_job())
    store = InMemoryExecutionStore()
    store.request_cancellation(
        submitted["job"]["job_id"],
        requested_at=100.5,
    )
    worker = TypedWorker(
        transport,
        store,
        "worker-a",
        monotonic_clock=lambda: 0.0,
        wall_clock=lambda: 102.0,
    )
    outcome = worker.run_once()[0]
    assert outcome["job"]["status"] == "cancelled"
    assert outcome["acknowledged"] is True
    assert store.is_cancelled(outcome["job"]["job_id"]) is False


def test_worker_persists_handler_failure():
    def validator(payload):
        return dict(payload)

    def handler(_payload, _context):
        raise RuntimeError("service unavailable")

    registry = TypedHandlerRegistry(
        [HandlerSpec("simulation.run", "simulation", 30, validator, handler)]
    )
    transport = InMemoryStreamTransport()
    transport.enqueue(_job())
    store = InMemoryExecutionStore()
    worker = TypedWorker(
        transport,
        store,
        "worker-a",
        registry=registry,
        monotonic_clock=lambda: 0.0,
        wall_clock=lambda: 102.0,
    )
    outcome = worker.run_once()[0]
    assert outcome["job"]["status"] == "failed"
    assert "service unavailable" in outcome["job"]["error"]
    assert store.get(outcome["job"]["job_id"])["status"] == "failed"


def test_worker_persists_before_acknowledgement():
    class FailingAckTransport(InMemoryStreamTransport):
        def acknowledge(self, message_id, worker_id, job):
            raise RuntimeError("ack unavailable")

    transport = FailingAckTransport()
    submitted = transport.enqueue(_job())
    store = InMemoryExecutionStore()
    worker = TypedWorker(
        transport,
        store,
        "worker-a",
        monotonic_clock=lambda: 0.0,
        wall_clock=lambda: 102.0,
    )
    try:
        worker.run_once()
    except RuntimeError as exc:
        assert "ack unavailable" in str(exc)
    else:
        raise AssertionError("acknowledgement failure was hidden")
    assert store.get(submitted["job"]["job_id"])["status"] == "succeeded"


def test_store_factory_falls_back_only_when_redis_is_unconfigured(monkeypatch):
    monkeypatch.delenv("REDIS_URL", raising=False)
    assert build_execution_store(redis_url=None).backend == "memory"
    try:
        build_execution_store(
            redis_url=None,
            allow_memory_fallback=False,
        )
    except RuntimeError as exc:
        assert "REDIS_URL" in str(exc)
    else:
        raise AssertionError("missing Redis configuration was accepted")
