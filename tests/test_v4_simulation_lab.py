from simulation_lab_v4 import (
    compare_scenarios,
    normalize_adjustments,
    sensitivity_analysis,
    simulate_game,
)


def _profile():
    return {
        "home_team": "Harbor Hawks",
        "away_team": "Metro Wolves",
        "home_points": 25,
        "away_points": 21,
        "home_sd": 9,
        "away_sd": 10,
        "simulations": 1200,
        "seed": 44,
    }


def test_simulation_is_seeded_and_distributional():
    first = simulate_game(_profile())
    second = simulate_game(_profile())

    assert first == second
    assert first["simulations"] == 1200
    assert first["margin"]["p10"] < first["margin"]["p50"] < first["margin"]["p90"]
    assert first["total"]["p10"] < first["total"]["p90"]
    assert round(first["win_probability"]["home"] + first["win_probability"]["away"], 4) == 1.0


def test_home_injury_adjustment_reduces_home_probability():
    baseline = simulate_game(_profile())
    injured = simulate_game(
        _profile(),
        [{"factor": "home_injuries", "side": "home", "impact": -5.0}],
    )

    assert injured["means"]["home"] == baseline["means"]["home"] - 5
    assert injured["win_probability"]["home"] < baseline["win_probability"]["home"]


def test_adjustments_are_bounded_and_filtered():
    result = normalize_adjustments(
        [
            {"factor": "weather", "side": "total", "impact": -99},
            {"factor": "unknown", "impact": 4},
            {"factor": "pace", "side": "invalid", "impact": 3},
        ]
    )

    assert len(result) == 2
    assert result[0]["impact"] == -12
    assert result[1]["side"] == "both"


def test_scenario_comparison_ranks_largest_delta_first():
    result = compare_scenarios(
        _profile(),
        [
            {
                "name": "Light rain",
                "adjustments": [{"factor": "weather", "side": "total", "impact": -1}],
            },
            {
                "name": "Home QB out",
                "adjustments": [{"factor": "home_injuries", "side": "home", "impact": -8}],
            },
        ],
    )

    assert result["scenarios"][0]["name"] == "Home QB out"
    assert abs(result["scenarios"][0]["home_probability_delta"]) >= abs(
        result["scenarios"][1]["home_probability_delta"]
    )


def test_sensitivity_analysis_reports_ranked_swings():
    result = sensitivity_analysis(
        _profile(),
        [
            {"factor": "weather", "side": "total", "step": 2},
            {"factor": "home_lineup", "side": "home", "step": 5},
            {"factor": "invalid", "side": "home", "step": 8},
        ],
    )

    assert len(result["sensitivity"]) == 2
    assert result["sensitivity"][0]["factor"] == "home_lineup"
    assert result["sensitivity"][0]["swing"] > 0
