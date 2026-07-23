from flask import Flask

from routes.v42_api import v42_bp


def _client():
    app = Flask(__name__)
    app.register_blueprint(v42_bp)
    return app.test_client()


def _job():
    return (
        _client()
        .post(
            "/api/v4.2/jobs/normalize",
            json={
                "job_type": "simulation.run",
                "payload": {
                    "home_win_probability": 0.55,
                    "trials": 1_000,
                    "seed": 7,
                },
                "submitted_at": 100.0,
            },
        )
        .get_json()
    )


def test_execution_capabilities_expose_handlers_and_limits():
    response = _client().get("/api/v4.2/execution/capabilities")
    body = response.get_json()
    assert response.status_code == 200
    assert body["version"] == "4.2.2"
    assert len(body["handlers"]) == 6
    assert body["features"]["idempotent_result_persistence"] is True


def test_execution_validation_returns_typed_contract():
    response = _client().post(
        "/api/v4.2/execution/jobs/validate",
        json={"job": _job()},
    )
    body = response.get_json()
    assert response.status_code == 200
    assert body["job_type"] == "simulation.run"
    assert body["family"] == "simulation"


def test_execution_validation_rejects_unknown_handler():
    job = _job()
    job["job_type"] = "python.eval"
    response = _client().post(
        "/api/v4.2/execution/jobs/validate",
        json={"job": job},
    )
    assert response.status_code == 400
    assert "no registered" in response.get_json()["error"]


def test_cancellation_endpoint_returns_stable_envelope():
    job = _job()
    response = _client().post(
        "/api/v4.2/execution/cancellations/normalize",
        json={
            "job_id": job["job_id"],
            "requested_at": 101.0,
            "reason": "operator request",
        },
    )
    body = response.get_json()
    assert response.status_code == 200
    assert body["job_id"] == job["job_id"]
    assert body["cancellation_id"].startswith("cancel_")


def test_combined_capabilities_advertise_execution_endpoints():
    body = _client().get("/api/v4.2/capabilities").get_json()
    assert body["version"] == "4.2.3"
    assert body["endpoints"]["execution_capabilities"].endswith("/execution/capabilities")
    assert body["endpoints"]["execution_validate"].endswith("/execution/jobs/validate")
