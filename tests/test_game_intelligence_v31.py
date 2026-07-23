from analytics_engine import game_intelligence


def _teams():
    home = {
        "name": "Harbor Hawks",
        "offense_epa": 0.19,
        "defense_epa_allowed": -0.04,
        "special_teams_epa": 0.01,
        "schedule_strength": 0.2,
        "recent_form": 0.15,
        "projected_points": 24,
        "fourth_down_aggressiveness": 0.7,
        "timeout_efficiency": 0.6,
        "late_game_efficiency": 0.8,
    }
    away = {
        "name": "Metro Wolves",
        "offense_epa": 0.03,
        "defense_epa_allowed": 0.08,
        "special_teams_epa": -0.01,
        "schedule_strength": -0.1,
        "recent_form": -0.05,
        "projected_points": 20,
        "fourth_down_aggressiveness": 0.4,
        "timeout_efficiency": 0.5,
        "late_game_efficiency": 0.45,
    }
    return home, away


def test_game_intelligence_is_deterministic_and_explainable():
    home, away = _teams()
    kwargs = {
        "home_injuries": [{"player": "WR1", "position": "WR", "status": "questionable"}],
        "away_injuries": [{"player": "QB1", "position": "QB", "status": "out"}],
        "weather": {"wind_mph": 18, "temperature_f": 35, "precipitation_probability": 0.2},
        "market": {"home_spread": -3.0},
        "simulations": 1500,
        "seed": 31,
    }
    first = game_intelligence(home, away, **kwargs)
    second = game_intelligence(home, away, **kwargs)

    assert first == second
    assert first["version"] == "3.1"
    assert first["favored_team"] == "Harbor Hawks"
    assert first["confidence_score"] >= 50
    assert len(first["key_factors"]) == 6
    assert [factor["rank"] for factor in first["key_factors"]] == list(range(1, 7))
    assert "model favors Harbor Hawks" in first["summary"]
    assert first["simulation"]["simulations"] == 1500


def test_adverse_weather_reduces_projected_total():
    home, away = _teams()
    clear = game_intelligence(home, away, weather={"wind_mph": 4}, simulations=500)
    severe = game_intelligence(
        home,
        away,
        weather={"wind_mph": 28, "temperature_f": 15, "precipitation_probability": 0.9},
        simulations=500,
    )

    clear_total = clear["projected_score"]["home"] + clear["projected_score"]["away"]
    severe_total = severe["projected_score"]["home"] + severe["projected_score"]["away"]
    assert severe_total < clear_total
    assert severe["weather"]["impact"] == "high"


def test_injury_difference_changes_model_margin():
    home, away = _teams()
    healthy = game_intelligence(home, away, simulations=500)
    injured_home = game_intelligence(
        home,
        away,
        home_injuries=[{"player": "QB1", "position": "QB", "status": "out"}],
        simulations=500,
    )
    assert injured_home["model_home_margin"] < healthy["model_home_margin"]
