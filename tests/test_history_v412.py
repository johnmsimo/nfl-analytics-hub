from history_v412 import (
    analyze_tendency_changes,
    compare_seasons,
    opponent_adjusted_splits,
    track_role_transitions,
)


def test_tendency_history_is_deterministic_and_ranks_largest_change():
    snapshots = [
        {
            "label": "11 | shotgun",
            "period": "weeks-1-4",
            "sort_key": 1,
            "snaps": 80,
            "usage_rate": 0.40,
            "success_rate": 0.44,
        },
        {
            "label": "11 | shotgun",
            "period": "weeks-5-8",
            "sort_key": 2,
            "snaps": 90,
            "usage_rate": 0.60,
            "success_rate": 0.55,
        },
        {
            "label": "12 | under-center",
            "period": "weeks-1-4",
            "sort_key": 1,
            "snaps": 30,
            "usage_rate": 0.20,
            "success_rate": 0.41,
        },
        {
            "label": "12 | under-center",
            "period": "weeks-5-8",
            "sort_key": 2,
            "snaps": 35,
            "usage_rate": 0.18,
            "success_rate": 0.42,
        },
    ]
    first = analyze_tendency_changes(snapshots)
    second = analyze_tendency_changes(snapshots)
    assert first == second
    assert first["changes"][0]["label"] == "11 | shotgun"
    assert first["changes"][0]["metrics"][0]["direction"] == "increased"


def test_tendency_history_reports_small_and_single_period_samples():
    result = analyze_tendency_changes(
        [
            {"label": "small", "period": "one", "snaps": 2, "usage_rate": 0.5},
            {"label": "single", "period": "one", "snaps": 20, "usage_rate": 0.5},
        ],
        min_snaps=10,
    )
    assert result["labels_analyzed"] == 0
    assert {row["label"] for row in result["insufficient_history"]} == {
        "single",
        "small",
    }


def test_role_history_detects_team_role_and_snap_share_changes():
    result = track_role_transitions(
        [
            {
                "player_id": "P1",
                "player_name": "Player One",
                "period": "2025",
                "sort_key": 1,
                "team": "A",
                "role": "WR3",
                "snaps": 100,
                "snap_share": 0.35,
            },
            {
                "player_id": "P1",
                "player_name": "Player One",
                "period": "2026",
                "sort_key": 2,
                "team": "B",
                "role": "WR1",
                "snaps": 200,
                "snap_share": 0.75,
            },
        ]
    )
    transition = result["transitions"][0]
    assert transition["events"] == [
        "team_change",
        "role_change",
        "snap_share_change",
    ]
    assert transition["snap_share_delta"] == 0.4
    assert result["current_roster"][0]["team"] == "B"


def test_role_history_ignores_unchanged_observations():
    result = track_role_transitions(
        [
            {
                "player_id": "P1",
                "period": "one",
                "team": "A",
                "role": "QB1",
                "snap_share": 0.98,
            },
            {
                "player_id": "P1",
                "period": "two",
                "team": "A",
                "role": "QB1",
                "snap_share": 0.99,
            },
        ]
    )
    assert result["transition_count"] == 0
    assert len(result["current_roster"]) == 1


def test_opponent_adjustment_uses_explicit_baselines_and_direction():
    result = opponent_adjusted_splits(
        [
            {
                "opponent": "A",
                "sample_size": 40,
                "success_rate": 0.56,
                "expected_success": 0.46,
                "pressure_rate": 0.18,
                "expected_pressure": 0.25,
            }
        ],
        [
            {
                "label": "Success",
                "metric": "success_rate",
                "baseline_metric": "expected_success",
                "scale": 1,
            },
            {
                "label": "Pressure",
                "metric": "pressure_rate",
                "baseline_metric": "expected_pressure",
                "direction": "lower",
                "scale": 1,
            },
        ],
    )
    split = result["adjusted_splits"][0]
    assert split["performance"] == "above_expected"
    assert all(metric["normalized_delta"] > 0 for metric in split["metrics"])
    assert result["metric_summary"][0]["sample_size"] == 40


def test_opponent_adjustment_reports_missing_and_small_sample_evidence():
    result = opponent_adjusted_splits(
        [
            {"opponent": "A", "sample_size": 2, "success_rate": 0.8},
            {"opponent": "B", "sample_size": 20, "success_rate": 0.5},
        ],
        [
            {
                "metric": "success_rate",
                "baseline_metric": "expected_success",
            }
        ],
        min_sample=10,
    )
    assert result["splits_adjusted"] == 0
    assert len(result["unavailable_evidence"]) == 2


def test_season_comparison_respects_metric_semantics():
    result = compare_seasons(
        [
            {
                "team_id": "A",
                "season": 2025,
                "snaps": 500,
                "success_rate": 0.44,
                "turnover_rate": 0.04,
            },
            {
                "team_id": "A",
                "season": 2026,
                "snaps": 520,
                "success_rate": 0.52,
                "turnover_rate": 0.02,
            },
        ],
        [
            {"metric": "success_rate", "scale": 1},
            {"metric": "turnover_rate", "direction": "lower", "scale": 1},
        ],
    )
    latest = result["latest_comparison"]
    assert latest["trend"] == "improved"
    assert {metric["outcome"] for metric in latest["metrics"]} == {"improved"}
    assert result["entity"]["id"] == "A"
    assert result["seasons_compared"] == 2


def test_season_comparison_requires_two_eligible_seasons():
    result = compare_seasons(
        [
            {"team_id": "A", "season": 2025, "snaps": 2, "success_rate": 0.44},
            {"team_id": "A", "season": 2026, "snaps": 20, "success_rate": 0.52},
        ],
        [{"metric": "success_rate"}],
        min_sample=10,
    )
    assert result["comparison_count"] == 0
    assert result["latest_comparison"] is None
    assert result["excluded_seasons"][0]["season"] == "2025"
