"""P3.2 player-stat and projection-readiness regression coverage."""
from __future__ import annotations

from pathlib import Path

import player_stats_warehouse
import projection_data
import projection_readiness
from database import db
from db_models import Player, PlayerTeamSeason, Season, Team

ROOT = Path(__file__).resolve().parents[1]


def test_preseason_evidence_does_not_replace_full_prior_baseline(monkeypatch):
    monkeypatch.setenv("P32_CURRENT_REG_WEEKS", "3")
    monkeypatch.setattr(
        projection_data,
        "regular_weeks_with_stats",
        lambda season: [1, 2] if season == 2026 else [],
    )
    monkeypatch.setattr(
        projection_data,
        "_row_count",
        lambda season: 5000 if season in {2025, 2026} else 0,
    )
    assert projection_data.stats_season(2026) == 2025

    monkeypatch.setattr(
        projection_data,
        "regular_weeks_with_stats",
        lambda season: [1, 2, 3] if season == 2026 else [],
    )
    assert projection_data.stats_season(2026) == 2026


def test_player_index_uses_current_roster_with_historical_evidence(app_fixture, monkeypatch):
    with app_fixture.app_context():
        db.session.rollback()
        existing_years = list(db.session.scalars(db.select(Season.year)).all())
        target = (max(existing_years) if existing_years else 2026) + 2000
        evidence = target - 1
        try:
            db.session.add_all([Season(year=evidence), Season(year=target)])
            old_team = Team(abbreviation=f"A{target}", name="P3.2 Old Team")
            new_team = Team(abbreviation=f"B{target}", name="P3.2 Current Team")
            player = Player(
                external_id=f"p32-overlay-{target}",
                espn_id=f"p32espn{target}",
                full_name="Projection Overlay Player",
                position="WR",
            )
            db.session.add_all([old_team, new_team, player])
            db.session.flush()
            db.session.add(
                PlayerTeamSeason(
                    player_id=player.id,
                    team_id=new_team.id,
                    season=target,
                    status="ACT",
                )
            )
            db.session.flush()
            key = str(player.espn_id)
            history = [
                {
                    "player_id": key,
                    "player_name": player.full_name,
                    "team": old_team.abbreviation,
                    "position": "WR",
                }
                for _ in range(6)
            ]
            monkeypatch.setattr(
                projection_data,
                "player_game_logs",
                lambda season: {key: history} if season == evidence else {},
            )

            index = projection_data.player_index(target, evidence)
            assert index[key]["team"] == new_team.abbreviation
            assert index[key]["games"] == 6
            assert index[key]["evidenceSeason"] == evidence
            assert index[key]["rosterSeason"] == target
            assert index[key]["rosterVerified"] is True
        finally:
            db.session.rollback()


def test_seeded_warehouse_exposes_normalized_player_game_logs(app_fixture):
    with app_fixture.app_context():
        logs = projection_data.player_game_logs(2025)
        assert logs
        sample = next(rows for rows in logs.values() if rows)
        row = sample[0]
        for field in ("player_id", "player_name", "team", "opponent", "position", "game_id"):
            assert field in row
        for field in projection_data.STAT_FIELDS:
            assert field in row


def test_current_stat_refresh_refreshes_schedule_first(tmp_path, monkeypatch):
    events: list[str] = []
    path = tmp_path / "player_week_2026.csv"
    path.write_text("header\n", encoding="utf-8")

    def fake_schedule(season, refresh=False):
        assert season == 2026
        assert refresh is True
        events.append("schedule")
        return [{"completed": True}, {"completed": False}]

    def fake_stats(season, refresh=False):
        assert season == 2026
        assert refresh is True
        assert events == ["schedule"]
        events.append("stats")
        return [{"player_id": "1"}]

    monkeypatch.setattr(player_stats_warehouse.nfl_data, "get_schedule", fake_schedule)
    monkeypatch.setattr(player_stats_warehouse.nfl_data, "get_player_week_stats", fake_stats)
    monkeypatch.setattr(player_stats_warehouse, "_runtime_player_week_path", lambda season: path)
    monkeypatch.setattr(
        player_stats_warehouse,
        "_import_with_provenance_cache",
        lambda incoming, source: {"read": 1, "written": 1, "skipped": 0},
    )

    result = player_stats_warehouse.refresh_current_stats(2026, object())
    assert events == ["schedule", "stats"]
    assert result["completed_games_discovered"] == 1
    assert result["cache_rows"] == 1
    assert result["written"] == 1


def test_projection_readiness_separates_returning_and_cold_start_players(monkeypatch):
    monkeypatch.setattr(projection_readiness.pd, "stats_season", lambda season: 2025)
    monkeypatch.setattr(
        projection_readiness.pd,
        "player_game_logs",
        lambda season: {
            "a": [{}, {}, {}, {}, {}],
            "b": [{}, {}],
            "d": [{}, {}, {}],
        },
    )
    monkeypatch.setattr(
        projection_readiness.pd,
        "player_index",
        lambda target, evidence: {
            "a": {"position": "QB", "rosterVerified": True},
            "b": {"position": "WR", "rosterVerified": True},
            "c": {"position": "RB", "rosterVerified": True},
            "d": {"position": "TE", "rosterVerified": True},
            "x": {"position": "CB", "rosterVerified": True},
        },
    )
    monkeypatch.setattr(
        projection_readiness.pd,
        "regular_weeks_with_stats",
        lambda season: [],
    )

    snapshot = projection_readiness.projection_pool_snapshot(2026)
    assert snapshot["current_skill_players"] == 4
    assert snapshot["returning_skill_players"] == 3
    assert snapshot["cold_start_skill_players"] == 1
    assert snapshot["projection_ready_skill_players"] == 2
    assert snapshot["projection_ready_skill_coverage"] == 0.5
    assert snapshot["projection_ready_returning_skill_coverage"] == 0.6667


def test_readiness_gate_uses_returning_player_coverage(monkeypatch):
    baseline = {
        "season": 2025,
        "player_game_rows": 5000,
        "players_with_stats": 600,
        "games_with_player_stats": 285,
        "regular_weeks": list(range(1, 19)),
        "player_season_rows": 500,
    }
    current = {
        "season": 2026,
        "player_game_rows": 250,
        "players_with_stats": 180,
        "games_with_player_stats": 16,
        "regular_weeks": [],
        "player_season_rows": 180,
    }
    projection = {
        "target_season": 2026,
        "evidence_season": 2025,
        "current_skill_players": 995,
        "returning_skill_players": 540,
        "cold_start_skill_players": 455,
        "projection_ready_skill_players": 462,
        "projection_ready_skill_coverage": 0.4643,
        "projection_ready_returning_skill_coverage": 0.8556,
    }
    monkeypatch.setattr(
        player_stats_warehouse,
        "_season_fact_snapshot",
        lambda season: baseline if season == 2025 else current,
    )
    monkeypatch.setattr(
        player_stats_warehouse,
        "projection_pool_snapshot",
        lambda season: projection,
    )

    result = player_stats_warehouse.player_stats_readiness_snapshot(2026, 2025)
    assert result["ok"] is True
    assert result["gates"]["projection_ready_coverage"] is True
    assert result["thresholds"]["projection_ready_coverage_denominator"] == (
        "returning_current_skill_players"
    )

    projection["projection_ready_returning_skill_coverage"] = 0.49
    result = player_stats_warehouse.player_stats_readiness_snapshot(2026, 2025)
    assert result["ok"] is False
    assert result["gates"]["projection_ready_coverage"] is False


def test_readiness_gate_requires_current_evidence(monkeypatch):
    baseline = {
        "season": 2025,
        "player_game_rows": 5000,
        "players_with_stats": 600,
        "games_with_player_stats": 285,
        "regular_weeks": list(range(1, 19)),
        "player_season_rows": 500,
    }
    current = {
        "season": 2026,
        "player_game_rows": 99,
        "players_with_stats": 80,
        "games_with_player_stats": 8,
        "regular_weeks": [],
        "player_season_rows": 80,
    }
    monkeypatch.setattr(
        player_stats_warehouse,
        "_season_fact_snapshot",
        lambda season: baseline if season == 2025 else current,
    )
    monkeypatch.setattr(
        player_stats_warehouse,
        "projection_pool_snapshot",
        lambda season: {
            "target_season": season,
            "evidence_season": 2025,
            "projection_ready_skill_players": 300,
            "projection_ready_skill_coverage": 0.45,
            "projection_ready_returning_skill_coverage": 0.75,
        },
    )
    result = player_stats_warehouse.player_stats_readiness_snapshot(2026, 2025)
    assert result["ok"] is False
    assert result["gates"]["current_completed_game_evidence"] is False


def test_projection_surfaces_use_warehouse_evidence_layer():
    props = (ROOT / "routes" / "props.py").read_text(encoding="utf-8")
    intelligence = (ROOT / "routes" / "intelligence.py").read_text(encoding="utf-8")
    for source in (props, intelligence):
        assert "import projection_data as pd" in source
        assert "nfl_data.player_game_logs(" not in source
        assert "nfl_data.player_index(" not in source
        assert "nfl_data.defense_vs_position(" not in source


def test_p32_workflow_is_protected_and_has_no_paid_provider_sync():
    workflow = (ROOT / ".github" / "workflows" / "p32-player-stats-sync.yml").read_text(
        encoding="utf-8"
    )
    assert "RUN_PLAYER_STATS_SYNC" in workflow
    assert "environment: production" in workflow
    assert "P32_TARGET_SEASON: '2026'" in workflow
    assert "P32_BASELINE_SEASON: '2025'" in workflow
    assert "/app/scripts/p32_player_stats_sync.py" in workflow
    assert "/app/scripts/p2_exit_verification.py" in workflow
    assert "sync_commercial" not in workflow
    assert "ODDS_API" not in workflow
