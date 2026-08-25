"""P1.3 regression coverage for honest dashboard data availability states."""

from __future__ import annotations

import routes.dashboard_api as dashboard_api


def _game() -> dict:
    return {
        "game_id": "game-1",
        "date": "2026-09-10T00:15:00Z",
        "season": 2026,
        "week": 1,
        "season_type": "REG",
        "home_team": "SEA",
        "away_team": "NE",
    }


def _team(team: str, wins: int = 10, ppg: float = 24.0, papg: float = 20.0) -> dict:
    return {
        "team": team,
        "games": 17,
        "wins": wins,
        "losses": 17 - wins,
        "ties": 0,
        "ppg": ppg,
        "papg": papg,
        "record": f"{wins}-{17 - wins}",
    }


def _all_teams() -> dict[str, dict]:
    teams = {"SEA": _team("SEA", 11, 26.0, 20.0), "NE": _team("NE", 8, 22.0, 21.0)}
    for i in range(30):
        code = f"T{i:02d}"
        teams[code] = _team(code, 9, 23.0, 22.0)
    return teams


def _stub_dashboard(
    monkeypatch, *, games=None, teams=None, projections=None, odds_configured=False, lines=None
):
    games = [] if games is None else games
    teams = {} if teams is None else teams
    projections = [] if projections is None else projections
    monkeypatch.setattr(
        dashboard_api.nfl_data, "current_week", lambda: {"season": 2026, "week": 1, "season_type": "REG"}
    )
    monkeypatch.setattr(dashboard_api.nfl_data, "stats_season", lambda season: 2025)
    monkeypatch.setattr(dashboard_api.nfl_data, "get_week_games", lambda season, week, stype: games)
    monkeypatch.setattr(dashboard_api.nfl_data, "team_summaries", lambda season: teams)
    monkeypatch.setattr(dashboard_api.odds_api, "is_configured", lambda: odds_configured)
    monkeypatch.setattr(dashboard_api, "_build_game_rows", lambda game, season: projections)
    if lines is not None:
        monkeypatch.setattr(dashboard_api, "game_lines", lambda game: lines)


def test_team_power_requires_real_played_game_inputs():
    assert dashboard_api._team_power({}) is None
    assert dashboard_api._team_power({"games": 0, "wins": 0, "ppg": 0, "papg": 0}) is None
    assert dashboard_api._team_power(_team("SEA")) is not None


def test_empty_dashboard_returns_null_kpis_and_unavailable_states(client, monkeypatch):
    _stub_dashboard(monkeypatch)

    response = client.get("/api/dashboard?season=2026&week=1&type=REG")
    assert response.status_code == 200
    body = response.get_json()

    assert body["featured"] is None
    assert body["trend"] == []
    assert body["kpis"] == {
        "win_probability": None,
        "prediction_confidence": None,
        "upside_score": None,
        "projected_points": None,
        "market_edge": None,
        "injury_impact": None,
    }
    assert body["data_status"]["status"] == "unavailable"
    assert body["engine"]["status"] == "unavailable"
    assert body["engine"]["data_coverage"] == 0
    assert body["data_status"]["components"]["trend_history"]["status"] == "unavailable"
    codes = {reason["code"] for reason in body["data_status"]["reasons"]}
    assert {"schedule_unavailable", "team_performance_incomplete", "market_pricing_unavailable"}.issubset(
        codes
    )


def test_missing_team_inputs_keep_schedule_but_withhold_prediction(client, monkeypatch):
    _stub_dashboard(monkeypatch, games=[_game()], teams={"SEA": _team("SEA")})

    body = client.get("/api/dashboard?season=2026&week=1&type=REG").get_json()

    assert body["featured"] is None
    assert body["data_status"]["status"] == "degraded"
    prediction = body["upcoming_games"][0]
    assert prediction["status"] == "unavailable"
    assert prediction["home_prob"] is None
    assert prediction["confidence"] is None
    assert prediction["projected_home"] is None
    assert "NE" in prediction["reason"]
    assert body["kpis"]["win_probability"] is None
    assert body["engine"]["covered_team_count"] == 1


def test_complete_inputs_return_only_derived_values(client, monkeypatch):
    projection = {
        "edge": 0.08,
        "modelProb": 0.61,
        "player": "Player",
        "playerId": "1",
        "team": "SEA",
        "marketLabel": "Receiving Yards",
        "modelMean": 71.2,
        "line": 68.5,
    }
    _stub_dashboard(
        monkeypatch,
        games=[_game()],
        teams=_all_teams(),
        projections=[projection],
        odds_configured=True,
        lines={"available": True, "h2h": {}, "spreads": {}, "totals": {}},
    )

    body = client.get("/api/dashboard?season=2026&week=1&type=REG").get_json()

    assert body["data_status"]["status"] == "ready"
    assert body["featured"]["status"] == "ready"
    assert body["kpis"]["win_probability"] == body["featured"]["home_prob"]
    assert body["kpis"]["projected_points"] == body["featured"]["projected_home"]
    assert body["kpis"]["market_edge"] == 0.08
    assert body["kpis"]["upside_score"] == 8.0
    assert body["kpi_status"]["market_edge"]["status"] == "ready"
    assert body["engine"]["data_coverage"] == 1.0
    assert body["trend"] == []
    assert {factor["source"] for factor in body["featured"]["factors"]} == {
        "team_performance",
        "model_assumption",
        "odds_provider",
    }


def test_model_only_projection_does_not_fabricate_market_edge(client, monkeypatch):
    projection = {"edge": None, "modelProb": 0.61}
    _stub_dashboard(
        monkeypatch, games=[_game()], teams=_all_teams(), projections=[projection], odds_configured=False
    )

    body = client.get("/api/dashboard?season=2026&week=1&type=REG").get_json()

    assert body["featured"]["status"] == "ready"
    assert body["kpis"]["market_edge"] is None
    assert body["kpis"]["upside_score"] is None
    assert body["kpi_status"]["market_edge"]["status"] == "unavailable"


def test_market_provider_failure_is_reported_as_degraded(client, monkeypatch):
    _stub_dashboard(monkeypatch, games=[_game()], teams=_all_teams(), odds_configured=True)

    def fail_lines(game):
        raise RuntimeError("provider failed")

    monkeypatch.setattr(dashboard_api, "game_lines", fail_lines)
    body = client.get("/api/dashboard?season=2026&week=1&type=REG").get_json()

    market = body["data_status"]["components"]["market_pricing"]
    assert market["status"] == "degraded"
    assert market["error_count"] == 1
    assert body["upcoming_games"][0]["market"]["status"] == "degraded"
    assert any(reason["code"] == "market_pricing_degraded" for reason in body["data_status"]["reasons"])


def test_dashboard_html_contains_no_fabricated_fallback_presentation(client):
    html = client.get("/").get_data(as_text=True)

    for fabricated in ("27.3", "10,000-simulation", "Team performance',72", "[22,31,38", "Upside score"):
        assert fabricated not in html
    assert "No simulation was run for this card" in html
    assert "Trend history unavailable" in html
    assert "Model inputs unavailable" in html
