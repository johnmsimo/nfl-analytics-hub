from analytics_engine.platform_v31 import (
    assistant_response,
    betting_intelligence,
    live_game_center,
    normalize_watchlist,
    player_intelligence,
    team_intelligence,
)


def test_live_center_probabilities_sum_to_one():
    result = live_game_center({"home_score": 24, "away_score": 21, "seconds_remaining": 120, "possession": "away"})
    assert round(result["home_win_probability"] + result["away_win_probability"], 4) == 1.0


def test_player_projection_and_bounds():
    result = player_intelligence({"name": "WR", "season_average": 14, "usage_rate": .2, "matchup_grade": 5, "recent_games": [{"fantasy_points": 18}]})
    assert result["floor"] <= result["projection"] <= result["ceiling"]


def test_team_and_betting_outputs():
    assert team_intelligence({"name": "A", "offense_epa": .2})["team"] == "A"
    result = betting_intelligence([{"market": "spread", "model_probability": .6, "decimal_odds": 2.0}])
    assert result["signals"][0]["edge"] == .1


def test_assistant_and_watchlist():
    assert assistant_response("What is the spread?")["intent"] == "betting"
    assert normalize_watchlist([{"type": "team", "id": "NYJ"}, {"type": "team", "id": "NYJ"}])["count"] == 1