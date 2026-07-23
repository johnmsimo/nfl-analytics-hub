from scouting_v41 import cluster_team_styles, personnel_tendencies, player_similarity


def test_player_similarity_ranks_closest_candidate():
    result = player_similarity(
        {"id": "target", "speed": 4.4, "yards_per_route": 2.5, "target_rate": 0.24},
        [
            {"id": "close", "speed": 4.42, "yards_per_route": 2.4, "target_rate": 0.23},
            {"id": "far", "speed": 4.8, "yards_per_route": 1.1, "target_rate": 0.1},
        ],
        metrics=["speed", "yards_per_route", "target_rate"],
    )
    assert result["matches"][0]["id"] == "close"
    assert result["matches"][0]["similarity"] > result["matches"][1]["similarity"]
    assert result["method"] == "range_normalized_euclidean"


def test_player_similarity_reports_coverage_and_bounds_limit():
    result = player_similarity(
        {"id": "target", "speed": 4.5, "targets": 100},
        [{"id": str(index), "speed": 4.5 + index / 100} for index in range(30)],
        metrics=["speed", "targets"],
        limit=100,
    )
    assert len(result["matches"]) == 25
    assert result["matches"][0]["feature_coverage"] == 0.5


def test_team_style_clustering_is_deterministic_and_separates_profiles():
    teams = [
        {"team_id": "A", "pass_rate": 0.7, "pace": 24, "motion": 0.8},
        {"team_id": "B", "pass_rate": 0.68, "pace": 25, "motion": 0.75},
        {"team_id": "C", "pass_rate": 0.4, "pace": 32, "motion": 0.2},
        {"team_id": "D", "pass_rate": 0.42, "pace": 31, "motion": 0.25},
    ]
    first = cluster_team_styles(teams, cluster_count=2)
    second = cluster_team_styles(teams, cluster_count=2)
    assert first == second
    member_sets = [{member["id"] for member in cluster["members"]} for cluster in first["clusters"]]
    assert {"A", "B"} in member_sets
    assert {"C", "D"} in member_sets


def test_team_style_clustering_handles_empty_input():
    result = cluster_team_styles([])
    assert result["clusters"] == []
    assert result["team_count"] == 0


def test_tendencies_aggregate_personnel_and_formations():
    plays = [
        {
            "personnel": "11",
            "formation": "shotgun",
            "play_type": "pass",
            "success": True,
            "yards_gained": 25,
        },
        {
            "personnel": "11",
            "formation": "shotgun",
            "play_type": "run",
            "success": False,
            "yards_gained": 2,
        },
        {
            "personnel": "12",
            "formation": "under-center",
            "play_type": "run",
            "success": True,
            "yards_gained": 6,
        },
    ]
    result = personnel_tendencies(plays)
    eleven = result["personnel"][0]
    assert eleven["label"] == "11"
    assert eleven["snaps"] == 2
    assert eleven["pass_rate"] == 0.5
    assert eleven["explosive_rate"] == 0.5


def test_tendencies_respect_minimum_sample():
    result = personnel_tendencies(
        [
            {"personnel": "11", "formation": "shotgun"},
            {"personnel": "12", "formation": "pistol"},
            {"personnel": "12", "formation": "pistol"},
        ],
        min_snaps=2,
    )
    assert [row["label"] for row in result["personnel"]] == ["12"]
    assert result["combinations"][0]["label"] == "12 | pistol"
