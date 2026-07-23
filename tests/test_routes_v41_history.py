from flask import Flask

from routes.v41_api import v41_bp


def _client():
    app = Flask(__name__)
    app.register_blueprint(v41_bp)
    return app.test_client()


def test_v412_capabilities_expose_history_endpoints():
    response = _client().get("/api/v4.1/capabilities")
    body = response.get_json()
    assert response.status_code == 200
    assert body["version"] == "4.1.2"
    assert body["features"]["scouting_history"] is True
    assert body["endpoints"]["history_seasons"].endswith("/history/seasons")


def test_tendency_history_endpoint_returns_changes():
    response = _client().post(
        "/api/v4.1/scouting/history/tendencies",
        json={
            "snapshots": [
                {
                    "label": "11",
                    "period": "one",
                    "sort_key": 1,
                    "snaps": 20,
                    "usage_rate": 0.4,
                },
                {
                    "label": "11",
                    "period": "two",
                    "sort_key": 2,
                    "snaps": 25,
                    "usage_rate": 0.6,
                },
            ],
            "metrics": ["usage_rate"],
        },
    )
    body = response.get_json()
    assert response.status_code == 200
    assert body["version"] == "4.1.2"
    assert body["changes"][0]["label"] == "11"


def test_role_history_endpoint_validates_snapshots():
    response = _client().post(
        "/api/v4.1/scouting/history/roles",
        json={"snapshots": "not-a-list"},
    )
    assert response.status_code == 400
    assert response.get_json()["error"] == "snapshots must be a list"


def test_opponent_adjusted_endpoint_returns_grounded_split():
    response = _client().post(
        "/api/v4.1/scouting/history/opponent-adjusted",
        json={
            "splits": [
                {
                    "opponent": "A",
                    "sample_size": 20,
                    "success_rate": 0.55,
                    "expected_success": 0.45,
                }
            ],
            "metrics": [
                {
                    "metric": "success_rate",
                    "baseline_metric": "expected_success",
                    "scale": 1,
                }
            ],
        },
    )
    body = response.get_json()
    assert response.status_code == 200
    assert body["adjusted_splits"][0]["performance"] == "above_expected"


def test_season_history_endpoint_compares_consecutive_seasons():
    response = _client().post(
        "/api/v4.1/scouting/history/seasons",
        json={
            "seasons": [
                {"team_id": "A", "season": 2025, "snaps": 20, "success_rate": 0.4},
                {"team_id": "A", "season": 2026, "snaps": 20, "success_rate": 0.5},
            ],
            "metrics": [{"metric": "success_rate", "scale": 1}],
        },
    )
    body = response.get_json()
    assert response.status_code == 200
    assert body["latest_comparison"]["trend"] == "improved"
