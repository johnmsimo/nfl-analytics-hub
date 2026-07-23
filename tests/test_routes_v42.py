from flask import Flask

from routes.v42_api import v42_bp


def _client():
    app = Flask(__name__)
    app.register_blueprint(v42_bp)
    return app.test_client()


def _job():
    return {
        "job_type": "model.evaluate",
        "payload": {"model": "power-v7"},
        "submitted_at": 100.0,
    }


def test_capabilities_expose_complete_distributed_platform():
    response = _client().get("/api/v4.2/capabilities")
    body = response.get_json()
    assert response.status_code == 200
    assert body["version"] == "4.2.3"
    assert body["job_contract_version"] == "4.2.0"
    assert body["features"]["idempotent_job_contracts"] is True
    assert body["features"]["redis_stream_transport"] is True
    assert body["features"]["typed_handlers"] is True
    assert body["features"]["namespaced_distributed_cache"] is True
    assert body["features"]["dead_letter_inspection"] is True


def test_job_normalize_returns_stable_contract():
    response = _client().post("/api/v4.2/jobs/normalize", json=_job())
    body = response.get_json()
    assert response.status_code == 200
    assert body["job_id"].startswith("job_")
    assert body["status"] == "queued"


def test_job_normalize_rejects_invalid_payload():
    response = _client().post(
        "/api/v4.2/jobs/normalize",
        json={"job_type": "INVALID TYPE", "payload": {}},
    )
    assert response.status_code == 400
    assert "job_type" in response.get_json()["error"]


def test_transition_endpoint_validates_worker_claim():
    job = _client().post("/api/v4.2/jobs/normalize", json=_job()).get_json()
    response = _client().post(
        "/api/v4.2/jobs/transitions/validate",
        json={
            "job": job,
            "target_status": "running",
            "worker_id": "worker-a",
            "occurred_at": 101.0,
        },
    )
    assert response.status_code == 200
    assert response.get_json()["attempt"] == 1


def test_transition_endpoint_rejects_invalid_jump():
    job = _client().post("/api/v4.2/jobs/normalize", json=_job()).get_json()
    response = _client().post(
        "/api/v4.2/jobs/transitions/validate",
        json={"job": job, "target_status": "succeeded", "occurred_at": 101.0},
    )
    assert response.status_code == 400
    assert "cannot transition" in response.get_json()["error"]


def test_event_endpoint_returns_inspectable_envelope():
    job = _client().post("/api/v4.2/jobs/normalize", json=_job()).get_json()
    response = _client().post(
        "/api/v4.2/jobs/events/normalize",
        json={
            "job": job,
            "event_type": "job.queued",
            "sequence": 1,
            "occurred_at": 100.0,
        },
    )
    body = response.get_json()
    assert response.status_code == 200
    assert body["job_id"] == job["job_id"]
    assert body["payload_digest"] == job["payload_digest"]
