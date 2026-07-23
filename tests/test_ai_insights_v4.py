from ai_insights_v4 import (
    confidence_reasoning,
    decision_history,
    evidence_recommendations,
    explain_prediction_change,
    upset_alert,
)


def test_prediction_change_explains_material_delta():
    result = explain_prediction_change(
        {"probability": 0.51},
        {"probability": 0.62},
        [
            {"name": "QB active", "impact": 0.07, "source": "injury-feed"},
            {"name": "weather improved", "impact": 0.03, "source": "weather-feed"},
        ],
    )
    assert result["direction"] == "up"
    assert result["material_change"] is True
    assert result["drivers"][0]["name"] == "QB active"
    assert result["unexplained_delta"] == 0.01


def test_prediction_change_bounds_bad_evidence():
    result = explain_prediction_change(
        {"probability": 0.5},
        {"probability": 0.6},
        [{"name": "bad", "impact": 4}, {"name": "skip", "impact": "x"}],
    )
    assert result["drivers"][0]["impact"] == 0.25
    assert len(result["drivers"]) == 1


def test_upset_alert_requires_side_disagreement_and_edge():
    alert = upset_alert(
        {"probability": 0.64, "side": "away"},
        {"probability": 0.45, "favored_side": "home"},
    )
    assert alert["triggered"] is True
    assert alert["severity"] == "high"

    no_alert = upset_alert(
        {"probability": 0.56, "side": "home"},
        {"probability": 0.51, "favored_side": "home"},
    )
    assert no_alert["triggered"] is False


def test_confidence_reasoning_uses_agreement_sample_and_freshness():
    strong = confidence_reasoning(
        {
            "probability": 0.77,
            "disagreement": 0.04,
            "sample_size": 700,
            "freshness_hours": 1,
        }
    )
    weak = confidence_reasoning(
        {
            "probability": 0.52,
            "disagreement": 0.7,
            "sample_size": 20,
            "freshness_hours": 100,
        }
    )
    assert strong["confidence_level"] == "high"
    assert weak["confidence_level"] == "low"
    assert strong["confidence_score"] > weak["confidence_score"]


def test_recommendations_are_grounded_and_sorted():
    result = evidence_recommendations(
        {
            "probability": 0.71,
            "disagreement": 0.1,
            "sample_size": 500,
            "freshness_hours": 2,
        },
        [
            {"id": "weather-1", "action": "Lower total exposure", "strength": 0.6, "source": "weather"},
            {"id": "injury-1", "action": "Reprice away offense", "strength": 0.9, "source": "injuries"},
        ],
    )
    assert result["grounded"] is True
    assert result["recommendations"][0]["evidence_id"] == "injury-1"
    assert result["count"] == 2


def test_low_confidence_adds_wait_recommendation():
    result = evidence_recommendations(
        {"probability": 0.5, "disagreement": 0.9, "sample_size": 0, "freshness_hours": 100},
        [],
    )
    assert result["recommendations"][0]["source"] == "confidence_reasoning"


def test_decision_history_tracks_material_changes():
    result = decision_history(
        [
            {"id": "a", "timestamp": "2026-07-23T10:00:00Z", "probability": 0.48, "side": "away"},
            {"id": "b", "timestamp": "2026-07-23T11:00:00Z", "probability": 0.50, "side": "away"},
            {"id": "c", "timestamp": "2026-07-23T12:00:00Z", "probability": 0.61, "side": "home"},
        ]
    )
    assert result["count"] == 3
    assert result["latest"]["id"] == "c"
    assert len(result["material_changes"]) == 1
    assert result["material_changes"][0]["side_changed"] is True


def test_empty_history_is_safe():
    result = decision_history([])
    assert result["latest"] is None
    assert result["events"] == []
