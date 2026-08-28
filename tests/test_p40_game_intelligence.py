from __future__ import annotations

import p40_game_intelligence as p40


def _game(game_id="g1", home="AAA", away="BBB"):
    return {
        "game_id": game_id,
        "season": 2026,
        "season_type": "REG",
        "week": 1,
        "date": "2026-09-10T00:00:00Z",
        "home_team": home,
        "away_team": away,
    }


def _profile(team, rating, quality=1.0, mode="prior-season-fallback", advanced=True):
    return {
        "team": team,
        "teamName": team,
        "targetSeason": 2026,
        "evidenceSeason": 2025,
        "evidenceMode": mode,
        "evidenceQuality": quality,
        "rating": rating,
        "basic": {"games": 17.0},
        "advanced": {"available": advanced},
    }


def test_game_probability_contract_is_bounded_and_complementary():
    row = p40.predict_game(_game(), _profile("AAA", 6.0), _profile("BBB", -4.0))
    assert 0.08 <= row["homeWinProbability"] <= 0.92
    assert 0.08 <= row["awayWinProbability"] <= 0.92
    assert abs(row["homeWinProbability"] + row["awayWinProbability"] - 1.0) < 1e-6
    assert row["selectedProbability"] >= 0.5
    assert row["decisionGrade"] in {"Strong Play", "Play", "Lean", "Pass"}
    assert row["actionable"] is False
    assert row["priceStatus"] == "model-only"


def test_weak_evidence_shrinks_probability_toward_fifty_percent():
    strong = p40.predict_game(
        _game(),
        _profile("AAA", 7.0, quality=1.0),
        _profile("BBB", -3.0, quality=1.0),
    )
    weak = p40.predict_game(
        _game(),
        _profile("AAA", 7.0, quality=0.30),
        _profile("BBB", -3.0, quality=0.30),
    )
    assert abs(weak["homeWinProbability"] - 0.5) < abs(strong["homeWinProbability"] - 0.5)
    assert weak["confidenceScore"] < strong["confidenceScore"]


def test_home_field_breaks_equal_team_tie_toward_home():
    row = p40.predict_game(
        _game(),
        _profile("AAA", 0.0, mode="current-season"),
        _profile("BBB", 0.0, mode="current-season"),
    )
    assert row["modelHomeMargin"] > 0
    assert row["homeWinProbability"] > 0.5
    assert row["selectedSide"] == "home"
    assert row["selectedTeam"] == "AAA"


def test_large_away_edge_selects_away_without_becoming_actionable():
    row = p40.predict_game(_game(), _profile("AAA", -7.0), _profile("BBB", 7.0))
    assert row["selectedSide"] == "away"
    assert row["selectedTeam"] == "BBB"
    assert row["selectedProbability"] > 0.5
    assert row["actionable"] is False


def test_week_report_is_ranked_by_decision_quality(monkeypatch):
    games = [_game("g1", "AAA", "BBB"), _game("g2", "CCC", "DDD")]
    profiles = {
        "AAA": _profile("AAA", 0.0),
        "BBB": _profile("BBB", 0.0),
        "CCC": _profile("CCC", 8.0),
        "DDD": _profile("DDD", -5.0),
    }
    monkeypatch.setattr(p40.nfl_data, "get_week_games", lambda *args, **kwargs: games)
    monkeypatch.setattr(p40, "build_team_profile", lambda team, season: profiles[team])

    report = p40.build_week_report(2026, 1, "REG")
    assert report["gameCount"] == 2
    assert report["decisionCount"] == 2
    assert report["actionableCount"] == 0
    assert report["marketActionability"] == "disabled-in-p4.0"
    rank = {"Strong Play": 0, "Play": 1, "Lean": 2, "Pass": 3}
    grades = [rank[row["decisionGrade"]] for row in report["decisions"]]
    assert grades == sorted(grades)


def test_week_report_skips_only_games_missing_team_evidence(monkeypatch):
    monkeypatch.setattr(
        p40.nfl_data,
        "get_week_games",
        lambda *args, **kwargs: [_game("g1", "AAA", "BBB")],
    )
    monkeypatch.setattr(
        p40,
        "build_team_profile",
        lambda team, season: _profile(team, 1.0) if team == "AAA" else None,
    )
    report = p40.build_week_report(2026, 1, "REG")
    assert report["decisionCount"] == 0
    assert report["skippedCount"] == 1
    assert report["skipped"][0]["reason"] == "missing_team_evidence"
