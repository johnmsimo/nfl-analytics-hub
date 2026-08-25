"""P2.1 cross-provider player identity contracts."""

from database import db
from db_models import (
    Game,
    Player,
    PlayerExternalIdentity,
    PlayerGameStat,
    PlayerTeamSeason,
    RawIngestRecord,
    Team,
)
from player_identity import reconcile_raw_player_identities, resolve_player
from source_registry import capture_raw, register_source


def test_trusted_bridge_consolidates_legacy_player_facts(app_fixture):
    with app_fixture.app_context():
        game = db.session.scalars(db.select(Game).where(Game.season == 2025)).first()
        assert game is not None
        team = db.session.get(Team, game.home_team_id)
        opponent = db.session.get(Team, game.away_team_id)
        canonical = Player(external_id="00-P21-BRIDGE", full_name="Bridge Player")
        legacy = Player(external_id="99111101", full_name="Bridge Player")
        db.session.add_all([canonical, legacy])
        db.session.flush()
        legacy_id = legacy.id
        db.session.add(
            PlayerExternalIdentity(
                player_id=legacy.id,
                source_key="espn",
                external_id="99111101",
            )
        )
        db.session.add(
            PlayerTeamSeason(
                player_id=legacy.id,
                team_id=team.id,
                season=2025,
            )
        )
        db.session.add(
            PlayerGameStat(
                game_id=game.id,
                player_id=legacy.id,
                team_id=team.id,
                opponent_id=opponent.id,
                home=True,
                attempts=7,
            )
        )
        db.session.flush()

        resolved = resolve_player(
            {"nflverse": "00-P21-BRIDGE", "espn": "99111101"},
            full_name="Bridge Player",
            position="QB",
        )

        assert resolved.id == canonical.id
        assert resolved.espn_id == "99111101"
        assert db.session.get(Player, legacy_id) is None
        assert (
            db.session.scalar(
                db.select(PlayerGameStat).where(PlayerGameStat.player_id == canonical.id)
            ).attempts
            == 7
        )
        assert (
            db.session.scalar(db.select(PlayerTeamSeason).where(PlayerTeamSeason.player_id == canonical.id))
            is not None
        )
        aliases = {
            (row.source_key, row.external_id)
            for row in db.session.scalars(
                db.select(PlayerExternalIdentity).where(PlayerExternalIdentity.player_id == canonical.id)
            ).all()
        }
        assert ("nflverse", "00-P21-BRIDGE") in aliases
        assert ("espn", "99111101") in aliases
        db.session.rollback()


def test_numeric_provider_ids_are_source_scoped(app_fixture):
    with app_fixture.app_context():
        espn = resolve_player(
            {"espn": "77220011"},
            full_name="ESPN Namespace Player",
        )
        sportsdata = resolve_player(
            {"sportsdataio": "77220011"},
            full_name="SportsData Namespace Player",
        )

        assert espn.id != sportsdata.id
        assert espn.external_id == "77220011"
        assert sportsdata.external_id == "sportsdataio:77220011"
        assert (
            db.session.scalar(
                db.select(PlayerExternalIdentity).where(
                    PlayerExternalIdentity.source_key == "espn",
                    PlayerExternalIdentity.external_id == "77220011",
                )
            ).player_id
            == espn.id
        )
        assert (
            db.session.scalar(
                db.select(PlayerExternalIdentity).where(
                    PlayerExternalIdentity.source_key == "sportsdataio",
                    PlayerExternalIdentity.external_id == "77220011",
                )
            ).player_id
            == sportsdata.id
        )
        db.session.rollback()


def test_existing_sportsdata_numeric_id_is_not_reused_for_espn(app_fixture):
    with app_fixture.app_context():
        sportsdata = Player(external_id="66554433", full_name="Legacy SportsData Player")
        db.session.add(sportsdata)
        db.session.flush()
        db.session.add(
            PlayerExternalIdentity(
                player_id=sportsdata.id,
                source_key="sportsdataio",
                external_id="66554433",
            )
        )
        db.session.flush()

        espn = resolve_player(
            {"espn": "66554433"},
            full_name="Different ESPN Player",
        )

        assert espn.id != sportsdata.id
        assert espn.external_id == "espn:66554433"
        assert (
            db.session.scalar(
                db.select(PlayerExternalIdentity).where(
                    PlayerExternalIdentity.source_key == "espn",
                    PlayerExternalIdentity.external_id == "66554433",
                )
            ).player_id
            == espn.id
        )
        db.session.rollback()


def test_unique_team_name_match_attaches_new_provider_identity(app_fixture):
    with app_fixture.app_context():
        team = db.session.scalars(db.select(Team)).first()
        player = Player(external_id="00-P21-TEAM", full_name="Unique Team Match")
        db.session.add(player)
        db.session.flush()
        db.session.add(PlayerTeamSeason(player_id=player.id, team_id=team.id, season=2025))
        db.session.flush()

        resolved = resolve_player(
            {"sportsdataio": "880022"},
            full_name="Unique Team Match",
            team_id=team.id,
            season=2025,
        )

        assert resolved.id == player.id
        identity = db.session.scalar(
            db.select(PlayerExternalIdentity).where(
                PlayerExternalIdentity.source_key == "sportsdataio",
                PlayerExternalIdentity.external_id == "880022",
            )
        )
        assert identity.player_id == player.id
        db.session.rollback()


def test_reconciliation_previews_then_merges_captured_roster_bridge(
    app_fixture,
    monkeypatch,
):
    with app_fixture.app_context():
        monkeypatch.setenv("PLAYER_IDENTITY_RECONCILE_LIMIT", "1")
        canonical = Player(external_id="00-P21-RAW", full_name="Raw Bridge Player")
        legacy = Player(external_id="99112233", full_name="Raw Bridge Player")
        db.session.add_all([canonical, legacy])
        source = register_source("p21-raw-identity", "P2.1 raw identity source")
        db.session.flush()
        db.session.add(
            PlayerExternalIdentity(
                player_id=legacy.id,
                source_key="espn",
                external_id="99112233",
            )
        )
        capture_raw(
            source,
            "roster",
            "2026:TST:00-P21-RAW",
            {
                "gsis_id": "00-P21-RAW",
                "espn_id": "99112233",
                "full_name": "Raw Bridge Player",
                "position": "WR",
            },
            season=2026,
        )
        db.session.flush()

        preview = reconcile_raw_player_identities(dry_run=True)
        assert preview["duplicate_player_sets"] == 1
        assert db.session.get(Player, legacy.id) is not None

        applied = reconcile_raw_player_identities(dry_run=False)
        assert applied["players_merged"] == 1
        assert db.session.get(Player, legacy.id) is None
        assert (
            db.session.scalar(db.select(RawIngestRecord).where(RawIngestRecord.source_id == source.id))
            is not None
        )

        merged = db.session.scalar(db.select(Player).where(Player.external_id == "00-P21-RAW"))
        db.session.delete(merged)
        db.session.delete(source)
        db.session.commit()


def test_identity_reconciliation_admin_requires_confirmation(client):
    rejected = client.post("/api/admin/player-identities/reconcile", json={})

    assert rejected.status_code == 400
    assert rejected.get_json()["error"] == "confirmation_required"
