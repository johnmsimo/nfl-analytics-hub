"""Importer contracts against the current nflverse schemas.

Each of these encodes a defect that made a whole dataset import zero rows.
"""

import external_providers as ep
from database import db
from db_models import Team, Season


def test_snap_game_key_parses_the_nflverse_id():
    row = {"game_id": "2025_01_ARI_NO"}
    assert ep._snap_game_key(row) == (1, "ARI", "NO")


def test_snap_game_key_canonicalizes_team_codes():
    """The feed writes JAX/WAS variants the warehouse stores canonically."""
    assert ep._snap_game_key({"game_id": "2025_19_BUF_JAX"}) == (19, "BUF", "JAC")


def test_snap_game_key_rejects_malformed_ids():
    for bad in ({"game_id": ""}, {"game_id": "2025_01_ARI"}, {}, {"game_id": "a_b_c_d"}):
        assert ep._snap_game_key(bad) is None or ep._snap_game_key(bad)[0] is None


def test_normalized_name_folds_punctuation_and_suffixes():
    assert ep._normalized_name("Ja'Marr Chase") == ep._normalized_name("JaMarr Chase")
    assert ep._normalized_name("Michael Pittman Jr.") == ep._normalized_name("Michael Pittman")
    assert ep._normalized_name("A.J.  Brown") == ep._normalized_name("AJ Brown")


def test_postseason_games_get_a_week_agnostic_key(app_fixture):
    """nflverse numbers the playoffs 19+; the schedule stores POST weeks 1-5."""
    with app_fixture.app_context():
        _, games = ep._game_lookup(2025)
        post_keys = [k for k in games if k[0] == "POST" and k[1] is None]
        assert post_keys, "postseason matchups must be registered without a week"
        for key in post_keys:
            assert games[key].season_type == "POST"


def test_a_wildcard_rematch_does_not_capture_regular_season_rows(app_fixture):
    """2024 had GB@PHI and WAS@TB in both week 1 and the wildcard round."""
    with app_fixture.app_context():
        _, games = ep._game_lookup(2024)
        for away, home in (("GB", "PHI"), ("WAS", "TB")):
            reg = ep._resolve_game(games, {"season_type": "REG"}, 1, away, home)
            post = ep._resolve_game(games, {"season_type": "POST"}, 19, away, home)
            assert reg is not None and post is not None
            assert reg.id != post.id, f"{away}@{home} week 1 resolved to the playoff game"
            assert reg.season_type == "REG" and post.season_type == "POST"


def test_unlabelled_rows_split_on_the_week_number(app_fixture):
    with app_fixture.app_context():
        _, games = ep._game_lookup(2025)
        post_key = next(k for k in games if k[0] == "POST" and k[1] is None)
        _, _, away, home = post_key
        assert ep._resolve_game(games, {}, 21, away, home) is games[post_key]


def test_player_index_prime_and_clear(app_fixture):
    with app_fixture.app_context():
        assert ep.prime_player_index() > 0
        assert ep._players_by_ext is not None
        hit = ep._ensure_player({"gsis_id": next(iter(ep._players_by_ext)), "full_name": "X"})
        assert hit is not None
        ep.clear_player_index()
        assert ep._players_by_ext is None


def test_ensure_player_records_the_pfr_id(app_fixture):
    with app_fixture.app_context():
        p = ep._ensure_player({"gsis_id": "00-TEST-PFR", "full_name": "Test Back", "pfr_id": "TestB00"})
        db.session.flush()
        assert p.pfr_id == "TestB00"


def test_injury_report_no_longer_requires_a_report_date(app_fixture):
    """The nflverse injury feed publishes no date; the grain is per week."""
    from db_models import InjuryReport

    with app_fixture.app_context():
        team = db.session.scalars(db.select(Team)).first()
        player = ep._ensure_player({"gsis_id": "00-TEST-INJ", "full_name": "Test End"})
        db.session.flush()
        item = InjuryReport(player_id=player.id, team_id=team.id, season=2025, week=4, report_date=None)
        db.session.add(item)
        db.session.flush()
        assert item.id is not None
        db.session.rollback()


class _FakeSlice:
    def __init__(self, rows):
        self._rows = rows

    def to_dicts(self):
        return list(self._rows)


class _FakeFrame:
    """Stands in for the polars frame nflreadpy returns."""

    def __init__(self, rows):
        self._rows = rows
        self.slices_taken = 0

    def iter_slices(self, n):
        for i in range(0, len(self._rows), n):
            self.slices_taken += 1
            yield _FakeSlice(self._rows[i : i + n])


def test_frame_rows_stream_in_slices_without_materializing():
    rows = [{"i": i} for i in range(45)]
    frame = _FakeFrame(rows)
    out = list(ep._iter_frame_rows(frame, chunk=20))
    assert out == rows
    assert frame.slices_taken == 3, "must consume the frame in slices, not one block"


def test_json_columns_accept_provider_date_types(app_fixture):
    """Feeds hand raw rows with dates straight into JSON columns."""
    import datetime as dt

    from db_models import InjuryReport

    with app_fixture.app_context():
        # Ensure season 2016 exists in the database
        season = db.session.get(Season, 2016)
        if not season:
            season = Season(year=2016)
            db.session.add(season)
            db.session.flush()

        team = db.session.scalars(db.select(Team)).first()
        player = ep._ensure_player({"gsis_id": "00-TEST-JSON", "full_name": "Test Guard"})
        db.session.flush()
        item = InjuryReport(
            player_id=player.id,
            team_id=team.id,
            season=2016,
            week=2,
            raw_payload={"date_modified": dt.datetime(2016, 9, 9), "seen": dt.date(2016, 9, 9)},
        )
        db.session.add(item)
        db.session.flush()  # would raise TypeError without the engine's encoder
        assert item.id is not None
        db.session.rollback()


def test_weekly_depth_charts_identify_by_club_code_and_week():
    """Charts through 2024 name the club `club_code` and carry no date."""
    row = {
        "club_code": "JAX",
        "week": 3,
        "depth_position": "S",
        "depth_team": "2",
        "gsis_id": "00-0030427",
        "full_name": "Legacy Player",
        "position": "SS",
    }
    assert ep._team(row.get("team") or row.get("club_code")) == "JAC"
    assert ep._int(row.get("pos_rank") or row.get("depth_rank") or row.get("depth_team")) == 2


def test_dated_depth_charts_identify_by_dt_and_pos_rank():
    """Snapshots from 2025 name the club `team` and carry no week."""
    row = {
        "team": "WSH",
        "dt": "2025-08-03T07:32:00",
        "pos_abb": "LDE",
        "pos_rank": "1",
        "gsis_id": "00-0034381",
        "player_name": "Dated Player",
    }
    assert ep._team(row.get("team") or row.get("club_code")) == "WAS"
    assert ep._date(row.get("dt")) is not None
    assert row.get("week") is None
