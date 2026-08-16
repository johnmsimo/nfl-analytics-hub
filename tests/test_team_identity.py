"""Warehouse team identity must be single-valued across both importers."""

from coverage_service import EXPECTED_TEAMS
from database import db
from db_models import Team
from external_providers import _team
from team_identity import normalize_team


def test_espn_codes_map_to_the_nflverse_canon():
    assert normalize_team("JAX") == "JAC"
    assert normalize_team("WSH") == "WAS"


def test_relocation_codes_collapse_to_current_franchise():
    assert normalize_team("LA") == "LAR"
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
    for espn_code in ("JAX", "WSH", "SEA"):
        assert _team(espn_code) == normalize_team(espn_code)


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
    """ESPN codes and Pro Bowl/placeholder entries must not become Team rows."""
    with app_fixture.app_context():
        stored = {t.abbreviation for t in db.session.scalars(db.select(Team)).all()}
    leaked = stored & {"JAX", "WSH", "AFC", "NFC", "TBD", "TBA", "NFL", "LA", "STL", "SD", "OAK"}
    assert not leaked, f"non-canonical team rows ingested: {leaked}"


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
