"""Warehouse team identity must be single-valued across both importers."""

import data_ingestion
from coverage_service import EXPECTED_TEAMS
from database import db
from db_models import Team
from external_providers import _team
from team_identity import normalize_team


def test_espn_codes_map_to_the_nflverse_canon():
    assert normalize_team("JAX") == "JAC"
    assert normalize_team("WSH") == "WAS"


def test_nflverse_documented_alternate_codes_map_to_canon():
    assert normalize_team("AZ") == "ARI"
    assert normalize_team("ARZ") == "ARI"
    assert normalize_team("BLT") == "BAL"
    assert normalize_team("CLV") == "CLE"
    assert normalize_team("HST") == "HOU"


def test_relocation_codes_collapse_to_current_franchise():
    assert normalize_team("LA") == "LAR"
    assert normalize_team("SL") == "LAR"
    assert normalize_team("STL") == "LAR"
    assert normalize_team("SD") == "LAC"
    assert normalize_team("OAK") == "LV"


def test_conference_and_placeholder_codes_are_not_teams():
    for code in ("AFC", "NFC", "TBD", "TBA", "NFL", "", None):
        assert normalize_team(code) is None


def test_canonical_codes_and_whitespace_are_stable():
    assert normalize_team("SEA") == "SEA"
    assert normalize_team(" jac ") == "JAC"


def test_both_importers_agree_on_the_canon():
    """The nflverse importer must resolve what the ESPN importer writes."""
    for provider_code in ("JAX", "WSH", "AZ", "ARZ", "BLT", "CLV", "HST", "SEA"):
        assert _team(provider_code) == normalize_team(provider_code)


def test_normalizer_covers_the_coverage_contract():
    """Every abbreviation the coverage contract expects is already canonical."""
    for abbr in EXPECTED_TEAMS:
        assert normalize_team(abbr) == abbr


def test_seed_ingests_every_franchise_once(app_fixture):
    """The cached ESPN files must seed all 32 franchises under canonical codes.

    Other suites add synthetic fixture teams to the shared session database, so
    this asserts on the franchises themselves rather than on the whole table.
    """
    with app_fixture.app_context():
        stored = {t.abbreviation for t in db.session.scalars(db.select(Team)).all()}
    assert set(EXPECTED_TEAMS) <= stored, f"franchises missing: {set(EXPECTED_TEAMS) - stored}"


def test_seed_never_creates_espn_or_placeholder_rows(app_fixture):
    """Provider aliases and placeholder entries must not become Team rows."""
    with app_fixture.app_context():
        stored = {t.abbreviation for t in db.session.scalars(db.select(Team)).all()}
    leaked = stored & {
        "JAX", "WSH", "AZ", "ARZ", "BLT", "CLV", "HST",
        "AFC", "NFC", "TBD", "TBA", "NFL", "LA", "SL", "STL", "SD", "OAK",
    }
    assert not leaked, f"non-canonical team rows ingested: {leaked}"


def test_cache_upsert_adopts_pre_normalization_team_row(app_fixture, monkeypatch):
    """A legacy row owning the provider id must be renamed, not duplicated.

    This mirrors production databases created before team normalization: the
    legacy abbreviation already owns ESPN's unique external id, so attempting
    to insert a second canonical Team row would raise an integrity error.
    """
    with app_fixture.app_context():
        legacy = Team(
            abbreviation="OLDX",
            name="Legacy Team",
            external_id="legacy-provider-id-9001",
        )
        db.session.add(legacy)
        db.session.commit()
        legacy_id = legacy.id

        original_normalize = data_ingestion.normalize_team

        def normalize_for_upgrade(value):
            if str(value or "").strip().upper() in {"OLDX", "NEWX"}:
                return "NEWX"
            return original_normalize(value)

        monkeypatch.setattr(data_ingestion, "normalize_team", normalize_for_upgrade)
        upgraded = data_ingestion._upsert_team(
            "OLDX",
            "Canonical Team",
            "legacy-provider-id-9001",
        )
        db.session.flush()

        assert upgraded.id == legacy_id
        assert upgraded.abbreviation == "NEWX"
        assert upgraded.name == "Canonical Team"
        assert db.session.scalar(
            db.select(db.func.count()).select_from(Team).where(
                Team.external_id == "legacy-provider-id-9001"
            )
        ) == 1

        db.session.delete(upgraded)
        db.session.commit()


def test_warehouse_routes_accept_espn_codes(client):
    """Frontend links carry ESPN codes and must still resolve in the warehouse."""
    for espn_code, canonical in (("JAX", "JAC"), ("WSH", "WAS"), ("SEA", "SEA")):
        resp = client.get(f"/api/data/teams/{espn_code}/profile")
        assert resp.status_code == 200, f"{espn_code} did not resolve"
        assert resp.get_json()["team"]["abbreviation"] == canonical


def test_team_profile_rejects_non_teams(client):
    """Conference and placeholder codes must 404 rather than resolve."""
    for code in ("AFC", "NFC", "TBD"):
        assert client.get(f"/api/data/teams/{code}/profile").status_code == 404
