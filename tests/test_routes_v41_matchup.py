from flask import Flask

from routes.v41_api import v41_bp


def _client():
    app = Flask(__name__)
    app.register_blueprint(v41_bp)
    return app.test_client()


def _payload():
    return {
        "offense": {"team_id": "OFF", "success_rate": 0.55, "snaps": 100},
        "defense": {"team_id": "DEF", "success_rate_allowed": 0.44, "snaps": 100},
        "metrics": [
            {
                "label": "Success rate",
                "offense_metric": "success_rate",
                "defense_metric": "success_rate_allowed",
                "scale": 1,
            }
        ],
    }


def test_v412_capabilities_preserve_matchup_endpoints():
    response = _client().get("/api/v4.1/capabilities")
    body = response.get_json()
    assert response.status_code == 200
    assert body["version"] == "4.1.2"
    assert body["features"]["matchup_intelligence"] is True
    assert body["endpoints"]["matchup_brief"].endswith("/matchups/brief")


def test_matchup_compare_endpoint_returns_grounded_comparison():
    response = _client().post("/api/v4.1/scouting/matchups/compare", json=_payload())
    body = response.get_json()
    assert response.status_code == 200
    assert body["version"] == "4.1.1"
    assert body["metrics_compared"] == 1
    assert body["offensive_advantages"][0]["label"] == "Success rate"


def test_matchup_compare_requires_explicit_metric_rules():
    payload = _payload()
    payload["metrics"] = []
    response = _client().post("/api/v4.1/scouting/matchups/compare", json=payload)
    assert response.status_code == 400
    assert response.get_json()["error"] == "metrics must be a non-empty list"


def test_matchup_brief_endpoint_combines_profile_and_tendency_evidence():
    payload = _payload()
    payload["offense_tendencies"] = [
        {
            "label": "11 | shotgun",
            "snaps": 60,
            "usage_rate": 0.5,
            "success_rate": 0.56,
            "explosive_rate": 0.14,
            "yards_per_play": 6.8,
        }
    ]
    payload["defense_tendencies"] = [
        {
            "label": "11 | shotgun",
            "snaps": 55,
            "usage_rate": 0.45,
            "success_rate": 0.46,
            "explosive_rate": 0.09,
            "yards_per_play": 5.2,
        }
    ]
    response = _client().post("/api/v4.1/scouting/matchups/brief", json=payload)
    body = response.get_json()
    assert response.status_code == 200
    assert body["grounded"] is True
    assert {row["source"] for row in body["ranked_evidence"]} == {"profile", "tendency"}
