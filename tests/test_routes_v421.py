from flask import Flask

from routes.v42_api import v42_bp


def _client():
    app = Flask(__name__)
    app.register_blueprint(v42_bp)
    return app.test_client()


def _running_job():
    client = _client()
    job = client.post(
        "/api/v4.2/jobs/normalize",
        json={
            "job_type": "model.evaluate",
            "payload": {"model": "power-v7"},
            "submitted_at": 100.0,
        },
    ).get_json()
    return client.post(
        "/api/v4.2/jobs/transitions/validate",
        json={
            "job": job,
            "target_status": "running",
            "worker_id": "worker-a",
            "occurred_at": 101.0,
        },
    ).get_json()


def test_transport_capabilities_expose_backends_and_limits():
    response = _client().get("/api/v4.2/transport/capabilities")
    body = response.get_json()
    assert response.status_code == 200
    assert body["version"] == "4.2.1"
    assert body["backends"] == ["redis", "memory"]
    assert body["features"]["consumer_groups"] is True


def test_lease_endpoint_returns_stable_contract():
    response = _client().post(
        "/api/v4.2/transport/leases/normalize",
        json={
            "job": _running_job(),
            "message_id": "1-0",
            "worker_id": "worker-a",
            "claimed_at": 101.0,
            "lease_seconds": 60,
        },
    )
    body = response.get_json()
    assert response.status_code == 200
    assert body["version"] == "4.2.1"
    assert body["expires_at"] == 161.0


def test_lease_endpoint_rejects_invalid_job():
    response = _client().post(
        "/api/v4.2/transport/leases/normalize",
        json={"job": {}, "message_id": "1-0", "worker_id": "worker-a"},
    )
    assert response.status_code == 400
    assert "contract" in response.get_json()["error"]


def test_capabilities_advertise_transport_endpoints():
    response = _client().get("/api/v4.2/capabilities")
    body = response.get_json()
    assert body["endpoints"]["transport_capabilities"].endswith(
        "/transport/capabilities"
    )
    assert body["endpoints"]["lease_normalize"].endswith(
        "/transport/leases/normalize"
    )
