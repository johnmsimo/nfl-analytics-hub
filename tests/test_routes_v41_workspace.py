from flask import Flask

from routes.v41_api import v41_bp


def _client():
    app = Flask(__name__)
    app.register_blueprint(v41_bp)
    return app.test_client()


def _report():
    return {
        "type": "matchup-card",
        "title": "PHI offense vs DAL defense",
        "source_endpoint": "/api/v4.1/scouting/matchups/brief",
        "result": {"ranked_evidence": [{"label": "Success rate"}]},
    }


def test_v413_capabilities_expose_workspace_endpoints():
    response = _client().get("/api/v4.1/capabilities")
    body = response.get_json()
    assert response.status_code == 200
    assert body["version"] == "4.1.3"
    assert body["features"]["scouting_workspace"] is True
    assert body["endpoints"]["workspace"].endswith("/scouting/workspace")


def test_workspace_manifest_discloses_local_report_storage():
    response = _client().get("/api/v4.1/scouting/workspace")
    body = response.get_json()
    assert response.status_code == 200
    assert body["version"] == "4.1.3"
    assert body["saved_reports"]["storage"] == "browser-local"
    assert body["saved_reports"]["server_persistence"] is False


def test_workspace_report_endpoint_normalizes_report():
    response = _client().post(
        "/api/v4.1/scouting/workspace/reports/normalize",
        json=_report(),
    )
    body = response.get_json()
    assert response.status_code == 200
    assert body["report"]["type"] == "matchup-card"
    assert len(body["report"]["report_id"]) == 20


def test_workspace_report_endpoint_rejects_invalid_contract():
    response = _client().post(
        "/api/v4.1/scouting/workspace/reports/normalize",
        json={"type": "unsupported"},
    )
    assert response.status_code == 400
    assert "supported workspace report type" in response.get_json()["error"]


def test_workspace_review_endpoint_returns_mobile_queue():
    response = _client().post(
        "/api/v4.1/scouting/workspace/reports/review",
        json={"reports": [_report()], "limit": 10},
    )
    body = response.get_json()
    assert response.status_code == 200
    assert body["reports_available"] == 1
    assert body["queue"][0]["evidence_count"] == 1


def test_workspace_review_endpoint_validates_reports():
    response = _client().post(
        "/api/v4.1/scouting/workspace/reports/review",
        json={"reports": "not-a-list"},
    )
    assert response.status_code == 400
    assert response.get_json()["error"] == "reports must be a list"
