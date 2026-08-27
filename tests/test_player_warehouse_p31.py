"""P3.1 player warehouse population and production-control coverage."""

from __future__ import annotations

from pathlib import Path

import player_warehouse
from database import db
from db_models import Player, PlayerExternalIdentity, PlayerTeamSeason, Season, Team


ROOT = Path(__file__).resolve().parents[1]


def test_height_normalization_accepts_roster_formats():
    assert player_warehouse._height_inches(74) == 74
    assert player_warehouse._height_inches("6-2") == 74
    assert player_warehouse._height_inches("6'2\"") == 74
    assert player_warehouse._height_inches("unknown") is None


def test_hydrate_player_fills_projection_ready_bio(app_fixture):
    with app_fixture.app_context():
        try:
            player = Player(external_id="p31-bio-test", full_name="Test Player")
            db.session.add(player)
            db.session.flush()
            player_warehouse._hydrate_player(
                player,
                {
                    "full_name": "Test Player Jr.",
                    "first_name": "Test",
                    "last_name": "Player",
                    "position": "wr",
                    "birth_date": "2000-01-02",
                    "height": "6-2",
                    "weight": "205",
                    "college_name": "Example State",
                },
            )
            assert player.full_name == "Test Player Jr."
            assert player.first_name == "Test"
            assert player.last_name == "Player"
            assert player.position == "WR"
            assert player.height_inches == 74
            assert player.weight_lbs == 205
            assert player.college == "Example State"
            assert player.birth_date.isoformat() == "2000-01-02"
        finally:
            db.session.rollback()


def test_player_warehouse_snapshot_enforces_coverage(app_fixture, monkeypatch):
    monkeypatch.setenv("P31_MIN_ROSTERED_PLAYERS", "2")
    monkeypatch.setenv("P31_MIN_TEAMS", "2")
    monkeypatch.setenv("P31_MIN_IDENTITY_COVERAGE", "1.0")
    monkeypatch.setenv("P31_MIN_NFLVERSE_COVERAGE", "1.0")
    monkeypatch.setenv("P31_MIN_POSITION_COVERAGE", "1.0")

    with app_fixture.app_context():
        db.session.rollback()
        existing_years = list(db.session.scalars(db.select(Season.year)).all())
        season = (max(existing_years) if existing_years else 2026) + 1000
        try:
            db.session.add(Season(year=season))
            teams = [
                Team(abbreviation="P31A", name="P3.1 Alpha"),
                Team(abbreviation="P31B", name="P3.1 Beta"),
            ]
            db.session.add_all(teams)
            db.session.flush()
            players = [
                Player(external_id="p31-00-a", full_name="Alpha Player", position="QB"),
                Player(external_id="p31-00-b", full_name="Beta Player", position="WR"),
            ]
            db.session.add_all(players)
            db.session.flush()
            db.session.add_all(
                [
                    PlayerExternalIdentity(
                        player_id=players[0].id,
                        source_key="nflverse",
                        external_id="p31-00-a",
                    ),
                    PlayerExternalIdentity(
                        player_id=players[1].id,
                        source_key="nflverse",
                        external_id="p31-00-b",
                    ),
                    PlayerTeamSeason(
                        player_id=players[0].id,
                        team_id=teams[0].id,
                        season=season,
                    ),
                    PlayerTeamSeason(
                        player_id=players[1].id,
                        team_id=teams[1].id,
                        season=season,
                    ),
                ]
            )
            db.session.flush()

            snapshot = player_warehouse.player_warehouse_snapshot(season)
            assert snapshot["ok"] is True
            assert snapshot["rostered_players"] == 2
            assert snapshot["teams_covered"] == 2
            assert snapshot["identity_coverage"] == 1.0
            assert snapshot["nflverse_identity_coverage"] == 1.0
            assert snapshot["position_coverage"] == 1.0
        finally:
            db.session.rollback()


def test_population_wrapper_uses_rosters_only(monkeypatch):
    monkeypatch.setattr(
        player_warehouse,
        "sync_rosters",
        lambda season: {"read": 1500, "written": 1500},
    )
    monkeypatch.setattr(
        player_warehouse,
        "normalize_roster_records",
        lambda season: {"processed": 1500, "normalized": 1500, "skipped": 0},
    )
    monkeypatch.setattr(
        player_warehouse,
        "player_warehouse_snapshot",
        lambda season: {"season": season, "ok": True, "rostered_players": 1500},
    )
    result = player_warehouse.populate_player_warehouse(2026)
    assert result["ok"] is True
    assert result["provider"] == "nflverse"
    assert result["dataset"] == "rosters"
    assert result["sync"] == {"read": 1500, "written": 1500}


def test_p31_workflow_is_protected_and_public_roster_only():
    workflow = (ROOT / ".github" / "workflows" / "p31-player-warehouse-sync.yml").read_text(
        encoding="utf-8"
    )
    assert "RUN_PLAYER_WAREHOUSE_SYNC" in workflow
    assert "environment: production" in workflow
    assert "P31_SEASON=2026" in workflow
    assert "/app/scripts/p31_player_warehouse_sync.py" in workflow
    assert "/app/scripts/p2_exit_verification.py" in workflow
    assert "sync_commercial" not in workflow
    assert "ODDS_API" not in workflow
