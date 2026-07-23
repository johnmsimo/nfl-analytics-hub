from flask import Flask

from distributed_v42 import normalize_job, transition_job
from execution_v422 import InMemoryExecutionStore
from operations_v423 import InMemoryDistributedCache
from routes import v42_api
from routes.v42_api import v42_bp
from transport_v421 import InMemoryStreamTransport


def _client():
    app = Flask(__name__)
    app.register_blueprint(v42_bp)
    return app.test_client()


def _memory_runtime(monkeypatch, transport=None):
    current_transport = transport or InMemoryStreamTransport()
    monkeypatch.setattr(
        v42_api,
        "build_distributed_cache",
        lambda: InMemoryDistributedCache(),
    )
    monkeypatch.setattr(v42_api, "build_transport", lambda: current_transport)
    monkeypatch.setattr(
        v42_api,
        "build_execution_store",
        lambda: InMemoryExecutionStore(),
    )
    return current_transport


def test_operations_capabilities_complete_v42_series():
    response = _client().get("/api/v4.2/operations/capabilities")
    body = response.get_json()
    assert response.status_code == 200
    assert body["version"] == "4.2.3"
    assert body["features"]["queue_depth_metrics"] is True
    assert body["scaling"]["redis"].startswith("required")


def test_cache_key_endpoint_returns_versioned_address():
    response = _client().post(
        "/api/v4.2/cache/keys/normalize",
        json={
            "namespace": "projections",
            "key": "player:13",
            "cache_version": 2,
        },
    )
    body = response.get_json()
    assert response.status_code == 200
    assert body["version"] == "4.2.3"
    assert ":projections:v2:" in body["storage_key"]


def test_cache_invalidation_endpoint_returns_stable_event():
    response = _client().post(
        "/api/v4.2/cache/invalidations/normalize",
        json={
            "namespace": "scouting",
            "tags": ["week-1"],
            "occurred_at": 101.0,
            "sequence": 3,
            "reason": "new data",
        },
    )
    body = response.get_json()
    assert response.status_code == 200
    assert body["event_id"].startswith("cache_evt_")
    assert body["event_type"] == "cache.invalidated"


def test_cache_invalidation_endpoint_rejects_missing_selector():
    response = _client().post(
        "/api/v4.2/cache/invalidations/normalize",
        json={"namespace": "scouting", "occurred_at": 101.0},
    )
    assert response.status_code == 400
    assert "requires" in response.get_json()["error"]


def test_operations_snapshot_reports_component_and_queue_health(monkeypatch):
    transport = _memory_runtime(monkeypatch)
    job = normalize_job(
        {
            "job_type": "simulation.run",
            "payload": {"home_win_probability": 0.55, "trials": 100, "seed": 7},
            "submitted_at": 100.0,
        },
        now=100.0,
    )
    transport.enqueue(job)
    response = _client().get("/api/v4.2/operations/snapshot")
    body = response.get_json()
    assert response.status_code == 200
    assert body["healthy"] is True
    assert len(body["components"]) == 3
    assert body["queue"]["queue_depth"] == 1


def test_dead_letter_endpoint_returns_payload_safe_records(monkeypatch):
    transport = _memory_runtime(monkeypatch)
    job = normalize_job(
        {
            "job_type": "simulation.run",
            "payload": {"secret": "not returned"},
            "submitted_at": 100.0,
        },
        now=100.0,
    )
    transport.enqueue(job)
    claim = transport.claim("worker-a", now=101.0)[0]
    failed = transition_job(
        claim["job"],
        "failed",
        now=102.0,
        error="failed safely",
    )
    transport.acknowledge(claim["message_id"], "worker-a", failed)
    response = _client().get("/api/v4.2/operations/dead-letters?limit=10")
    body = response.get_json()
    assert response.status_code == 200
    assert body["count"] == 1
    assert body["dead_letters"][0]["error"] == "failed safely"
    assert "payload" not in body["dead_letters"][0]


def test_operations_snapshot_surfaces_configured_backend_failure(monkeypatch):
    def unavailable():
        raise RuntimeError("Redis unavailable")

    monkeypatch.setattr(v42_api, "build_distributed_cache", unavailable)
    response = _client().get("/api/v4.2/operations/snapshot")
    assert response.status_code == 503
    assert response.get_json()["healthy"] is False
