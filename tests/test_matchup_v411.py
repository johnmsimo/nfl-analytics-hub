from matchup_v411 import compare_matchup_profiles, compare_tendencies, matchup_brief


def _profiles():
    offense = {
        "team_id": "OFF",
        "success_rate": 0.55,
        "explosive_rate": 0.16,
        "pressure_rate": 0.18,
        "snaps": 120,
    }
    defense = {
        "team_id": "DEF",
        "success_rate_allowed": 0.44,
        "explosive_rate_allowed": 0.09,
        "pressure_rate": 0.27,
        "snaps": 140,
    }
    metrics = [
        {
            "label": "Success rate",
            "offense_metric": "success_rate",
            "defense_metric": "success_rate_allowed",
            "scale": 1,
            "weight": 2,
        },
        {
            "label": "Explosive rate",
            "offense_metric": "explosive_rate",
            "defense_metric": "explosive_rate_allowed",
            "scale": 1,
        },
        {
            "label": "Pressure exposure",
            "offense_metric": "pressure_rate",
            "defense_metric": "pressure_rate",
            "direction": "lower",
            "scale": 1,
        },
    ]
    return offense, defense, metrics


def test_profile_matchup_finds_offensive_advantages_and_is_deterministic():
    offense, defense, metrics = _profiles()
    first = compare_matchup_profiles(offense, defense, metrics)
    second = compare_matchup_profiles(offense, defense, metrics)
    assert first == second
    assert first["lean"] == "offense"
    assert {row["label"] for row in first["offensive_advantages"]} == {
        "Explosive rate",
        "Pressure exposure",
        "Success rate",
    }
    assert first["confidence"]["level"] == "high"


def test_profile_matchup_respects_lower_favors_offense_direction():
    offense, defense, metrics = _profiles()
    result = compare_matchup_profiles(offense, defense, metrics)
    pressure = next(row for row in result["comparisons"] if row["label"] == "Pressure exposure")
    assert pressure["direction"] == "lower_favors_offense"
    assert pressure["normalized_edge"] > 0
    assert pressure["advantage"] == "offense"


def test_profile_matchup_reports_missing_metrics_and_coverage():
    result = compare_matchup_profiles(
        {"team": "A", "success_rate": 0.5},
        {"team": "B"},
        [
            {
                "label": "Success",
                "offense_metric": "success_rate",
                "defense_metric": "success_rate_allowed",
            },
            {"label": "Incomplete rule"},
        ],
    )
    assert result["metrics_compared"] == 0
    assert result["feature_coverage"] == 0.0
    assert len(result["unavailable_metrics"]) == 2
    assert result["lean"] == "even"


def test_profile_matchup_bounds_evidence_limit():
    metrics = [
        {
            "label": f"Metric {index}",
            "offense_metric": f"metric_{index}",
            "defense_metric": f"metric_{index}",
            "scale": 1,
        }
        for index in range(30)
    ]
    offense = {"snaps": 100, **{f"metric_{index}": 1 for index in range(30)}}
    defense = {"snaps": 100, **{f"metric_{index}": 0 for index in range(30)}}
    result = compare_matchup_profiles(offense, defense, metrics, limit=100)
    assert len(result["offensive_advantages"]) == 25
    assert len(result["exploitable_weaknesses"]) == 25


def _tendencies():
    offense = [
        {
            "label": "11 | shotgun",
            "snaps": 80,
            "usage_rate": 0.60,
            "success_rate": 0.56,
            "explosive_rate": 0.14,
            "yards_per_play": 6.8,
        },
        {
            "label": "12 | under-center",
            "snaps": 20,
            "usage_rate": 0.15,
            "success_rate": 0.39,
            "explosive_rate": 0.05,
            "yards_per_play": 3.7,
        },
    ]
    defense = [
        {
            "label": "11 | shotgun",
            "snaps": 70,
            "usage_rate": 0.55,
            "success_rate": 0.46,
            "explosive_rate": 0.09,
            "yards_per_play": 5.2,
        },
        {
            "label": "12 | under-center",
            "snaps": 25,
            "usage_rate": 0.20,
            "success_rate": 0.48,
            "explosive_rate": 0.10,
            "yards_per_play": 5.0,
        },
    ]
    return offense, defense


def test_tendency_matchup_ranks_opportunity_and_risk():
    offense, defense = _tendencies()
    result = compare_tendencies(offense, defense, min_snaps=10)
    assert result["labels_compared"] == 2
    assert result["opportunities"][0]["label"] == "11 | shotgun"
    assert result["risks"][0]["label"] == "12 | under-center"
    assert result["defense_metric_semantics"] == "rates_allowed"


def test_tendency_matchup_filters_small_samples_and_reports_unmatched():
    offense, defense = _tendencies()
    offense.append(
        {
            "label": "empty",
            "snaps": 3,
            "success_rate": 0.8,
            "explosive_rate": 0.5,
            "yards_per_play": 12,
        }
    )
    defense.append({"label": "empty", "snaps": 2, "success_rate": 0.2})
    offense.append({"label": "offense-only", "snaps": 30})
    result = compare_tendencies(offense, defense, min_snaps=10)
    assert result["insufficient_samples"][0]["label"] == "empty"
    assert result["unmatched_labels"]["offense_only"] == ["offense-only"]


def test_matchup_brief_ranks_supplied_profile_and_tendency_evidence():
    offense, defense, metrics = _profiles()
    offense_tendencies, defense_tendencies = _tendencies()
    profiles = compare_matchup_profiles(offense, defense, metrics)
    tendencies = compare_tendencies(offense_tendencies, defense_tendencies)
    result = matchup_brief(profiles, tendencies, limit=3)
    assert result["grounded"] is True
    assert result["lean"] == "offense"
    assert result["strengths"]
    assert result["risks"]
    assert result["ranked_evidence"] == sorted(
        result["ranked_evidence"],
        key=lambda row: (-row["evidence_score"], row["source"], row["label"]),
    )


def test_matchup_brief_handles_no_evidence():
    result = matchup_brief({"offense": {"id": "A"}, "defense": {"id": "B"}})
    assert result["lean"] == "even"
    assert result["evidence_count"] == 0
    assert result["summary"] == "Insufficient supplied evidence for a matchup lean."
