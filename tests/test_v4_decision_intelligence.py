from ai_decision_v4 import decision_brief, ensemble_decision, scenario_decision


def _models():
    return [
        {
            "name": "power-model",
            "version": "4.0-a",
            "probability": 0.68,
            "calibration": 0.92,
            "recency": 0.95,
            "sample_size": 5000,
        },
        {
            "name": "market-model",
            "version": "4.0-b",
            "probability": 0.58,
            "calibration": 0.82,
            "recency": 0.9,
            "sample_size": 8000,
        },
        {
            "name": "simulation-model",
            "version": "4.0-c",
            "probability": 63,
            "calibration": 0.88,
            "recency": 0.85,
            "sample_size": 10000,
        },
    ]


def test_ensemble_is_reliability_weighted_and_explainable():
    result = ensemble_decision(_models())
    assert result["status"] == "ok"
    assert result["version"] == "4.0"
    assert 0.58 < result["probability"] < 0.68
    assert result["decision"] == "home"
    assert result["primary_model"] == "power-model"
    assert round(sum(row["weight"] for row in result["models"]), 5) == 1.0


def test_invalid_models_are_skipped_safely():
    result = ensemble_decision([{"name": "bad", "probability": "not-a-number"}])
    assert result["status"] == "insufficient_models"


def test_scenario_changes_decision_and_ranks_drivers():
    result = scenario_decision(
        {"probability": 0.56},
        [
            {"name": "QB inactive", "probability_delta": -0.12, "reason": "starter ruled out"},
            {"name": "weather", "probability_delta": -0.02, "reason": "high wind"},
            {"name": "inactive scenario", "probability_delta": 0.3, "active": False},
        ],
    )
    assert result["decision"] == "away"
    assert result["adjusted_probability"] == 0.42
    assert result["biggest_driver"]["name"] == "QB inactive"


def test_decision_brief_classifies_risk():
    ensemble = ensemble_decision(_models())
    scenario = scenario_decision(ensemble, [{"name": "injury", "probability_delta": -0.03}])
    brief = decision_brief(ensemble, scenario)
    assert brief["grounded"] is True
    assert brief["risk"] in {"low", "moderate", "high"}
    assert "v4 ensemble" in brief["summary"]
