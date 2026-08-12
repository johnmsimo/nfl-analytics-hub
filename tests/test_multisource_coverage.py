from coverage_service import expected_team_abbreviations, summarize_team_coverage


def test_expected_team_contract_contains_all_32_franchises():
    teams = expected_team_abbreviations()
    assert len(teams) == 32
    assert len(set(teams)) == 32
    assert "JAC" in teams
    assert "LAR" in teams


def test_team_coverage_reports_missing_franchises():
    report = summarize_team_coverage(["BUF", "KC", "not-an-nfl-team"])
    assert report["covered_team_count"] == 2
    assert report["expected_team_count"] == 32
    assert report["coverage_pct"] == 6.2
    assert report["missing_teams"][:2] == ["ARI", "ATL"]
    assert report["status"] == "partial"


def test_team_coverage_is_complete_only_for_the_canonical_set():
    report = summarize_team_coverage(expected_team_abbreviations())
    assert report["coverage_pct"] == 100.0
    assert report["missing_teams"] == []
    assert report["status"] == "complete"
