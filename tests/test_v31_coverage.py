from analytics_engine import game_intelligence
from analytics_engine.platform_v31 import (
    assistant_response,
    betting_intelligence,
    live_game_center,
    normalize_watchlist,
    player_intelligence,
    team_intelligence,
)


def test_live_center_states_possession_and_invalid_values():
    pregame = live_game_center({})
    home_ball = live_game_center(
        {"home_score": 10, "away_score": 10, "seconds_remaining": 1800, "possession": "home"}
    )
    away_ball = live_game_center(
        {"home_score": 10, "away_score": 10, "seconds_remaining": 1800, "possession": "away"}
    )
    final = live_game_center(
        {"home_score": "bad", "away_score": 7, "seconds_remaining": -10}
    )
    assert pregame["state"] == "pregame"
    assert home_ball["state"] == "live"
    assert home_ball["home_win_probability"] > away_ball["home_win_probability"]
    assert final["state"] == "final"


def test_player_intelligence_trends_peers_and_injury():
    rising = player_intelligence(
        {
            "name": "Rising WR",
            "season_average": 10,
            "usage_rate": 0.25,
            "matchup_grade": 4,
            "recent_games": [
                {"fantasy_points": 6},
                {"fantasy_points": 10},
                {"fantasy_points": 16},
                {"fantasy_points": 20},
            ],
        },
        peers=[
            {"name": "Far", "season_average": 20, "usage_rate": 0.1},
            {"name": "Near", "season_average": 13, "usage_rate": 0.24},
        ],
    )
    falling = player_intelligence(
        {
            "name": "Falling RB",
            "season_average": 20,
            "injury_risk": 1,
            "recent_games": [{"fantasy_points": 8}, {"fantasy_points": 7}],
        }
    )
    steady = player_intelligence({"name": "Steady QB", "season_average": "bad"})
    assert rising["trend"] == "up"
    assert rising["similar_players"][0]["name"] == "Near"
    assert falling["trend"] == "down"
    assert steady["trend"] == "steady"
    assert steady["floor"] == 0


def test_team_tiers_strengths_and_weaknesses():
    elite = team_intelligence(
        {
            "name": "Elite",
            "offense_epa": 0.3,
            "defense_epa_allowed": -0.2,
            "special_teams_epa": 0.1,
            "recent_form": 0.5,
        }
    )
    contender = team_intelligence({"team": "Contender", "offense_epa": 0.1})
    average = team_intelligence({"name": "Average"})
    retooling = team_intelligence(
        {"name": "Retooling", "offense_epa": -0.2, "defense_epa_allowed": 0.2}
    )
    assert elite["tier"] == "elite"
    assert contender["tier"] == "contender"
    assert average["tier"] == "average"
    assert retooling["tier"] == "retooling"
    assert retooling["weaknesses"]


def test_betting_grades_defaults_and_actionable_rows():
    result = betting_intelligence(
        [
            {"market": "a", "model_probability": 0.65, "decimal_odds": 2.0},
            {"market": "b", "model_probability": 0.56, "decimal_odds": 2.0},
            {"market": "c", "model_probability": 0.53, "decimal_odds": 2.0},
            {"market": "pass", "model_probability": "bad", "decimal_odds": 0},
        ],
        bankroll=200,
    )
    grades = {row["market"]: row["grade"] for row in result["signals"]}
    assert grades == {"a": "A", "b": "B", "c": "C", "pass": "pass"}
    assert len(result["actionable"]) == 3


def test_assistant_all_intents_and_grounding():
    assert assistant_response("   ")["intent"] == "empty"
    assert assistant_response("Who wins this game?")["intent"] == "game"
    assert assistant_response("Player yard projection?")["intent"] == "player"
    assert assistant_response("Best odds edge?")["intent"] == "betting"
    assert assistant_response("Team playoff ranking?")["intent"] == "team"
    general = assistant_response("Explain this", {"summary": "Grounded answer"})
    assert general["intent"] == "general"
    assert general["grounded"] is True


def test_watchlist_normalization_empty_and_deduplicated():
    result = normalize_watchlist(
        [
            {"type": "TEAM", "id": "BUF"},
            {"type": "team", "id": "BUF", "label": "Bills"},
            {"type": "player", "name": "Josh Allen"},
            {"type": "game", "id": ""},
        ]
    )
    assert result["count"] == 2
    assert result["items"][0]["type"] == "team"


def _balanced_teams():
    home = {
        "name": "Home",
        "offense_epa": 0.05,
        "defense_epa_allowed": 0.05,
        "projected_points": 21,
        "home_field_points": 0,
    }
    away = {
        "name": "Away",
        "offense_epa": 0.05,
        "defense_epa_allowed": 0.05,
        "projected_points": 21,
    }
    return home, away


def test_game_intelligence_close_game_and_moderate_weather():
    home, away = _balanced_teams()
    result = game_intelligence(
        home,
        away,
        weather={"wind_mph": 18, "temperature_f": 40},
        simulations=250,
        seed=7,
    )
    assert result["weather"]["impact"] == "moderate"
    assert result["game_script"] == "one-score game with late-possession leverage"


def test_game_intelligence_away_favorite_and_fallback_names():
    home = {
        "name": "",
        "offense_epa": -0.2,
        "defense_epa_allowed": 0.2,
        "projected_points": 14,
        "home_field_points": 0,
    }
    away = {
        "team": "Road Power",
        "offense_epa": 0.3,
        "defense_epa_allowed": -0.2,
        "projected_points": 30,
        "fourth_down_aggressiveness": 1,
        "timeout_efficiency": 1,
        "late_game_efficiency": 1,
    }
    result = game_intelligence(
        home,
        away,
        home_injuries=[{"position": "QB", "status": "out"}],
        market={"home_spread": 10},
        simulations=250,
        seed=9,
    )
    assert result["home_team"] == "Home"
    assert result["favored_side"] == "away"
    assert "controls the game script" in result["game_script"]
    assert result["biggest_risk"]
