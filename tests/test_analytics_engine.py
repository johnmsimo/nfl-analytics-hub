from analytics_engine import (
    drive_success_summary,
    epa_summary,
    injury_impact,
    live_win_probability,
    matchup_intelligence,
    monte_carlo_game,
    player_similarity,
    power_rating,
)


def test_live_win_probability_moves_with_score_and_clock():
    tied = live_win_probability(0, 900)
    leading = live_win_probability(7, 120)
    assert leading["home_win_probability"] > tied["home_win_probability"]
    assert leading["away_win_probability"] < tied["away_win_probability"]


def test_epa_and_drive_summaries():
    epa = epa_summary([{"epa": 0.5}, {"epa": -0.2}, {"epa": 0.7}])
    drives = drive_success_summary([{"points": 7}, {"points": 0}, {"points": 3}])
    assert epa == {"plays": 3, "total_epa": 1.0, "epa_per_play": 0.333, "success_rate": 0.6667}
    assert drives["scoring_rate"] == 0.6667
    assert drives["touchdown_rate"] == 0.3333


def test_monte_carlo_is_reproducible():
    first = monte_carlo_game(24, 21, simulations=1000, seed=13)
    second = monte_carlo_game(24, 21, simulations=1000, seed=13)
    assert first == second
    assert first["simulations"] == 1000


def test_power_and_injury_models_are_explainable():
    rating = power_rating(0.15, -0.05, special_teams_epa=0.02)
    injuries = injury_impact([{"player": "QB1", "position": "QB", "status": "out"}])
    assert rating["rating"] > 50
    assert rating["offense"] > 0
    assert injuries["rating_penalty"] == 8.0
    assert injuries["players"][0]["impact"] == 1.0


def test_player_similarity_orders_best_match_first():
    matches = player_similarity(
        {"epa": 0.2, "success": 0.5},
        {
            "near": {"epa": 0.21, "success": 0.49},
            "far": {"epa": -0.4, "success": 0.2},
        },
    )
    assert matches[0]["player"] == "near"
    assert matches[0]["similarity"] > matches[1]["similarity"]


def test_matchup_intelligence_includes_reasons():
    result = matchup_intelligence(
        {"offense_epa": 0.2, "defense_epa_allowed": -0.05},
        {"offense_epa": 0.0, "defense_epa_allowed": 0.1},
    )
    assert result["favored_team"] == "home"
    assert result["estimated_point_edge"] > 0
    assert len(result["reasons"]) == 2
