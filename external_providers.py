"""External provider integrations.

The default implementation uses nflverse public release assets. Commercial
providers can be added behind the same interface without changing warehouse
models or admin APIs.
"""
from __future__ import annotations

import csv
import gzip
import io
import os
import re
from datetime import date, datetime, timezone
from typing import Iterable

import requests

from database import db
from db_models import (DataSource, DataSyncRun, DepthChartEntry, Game, InjuryReport,
                       Player, PlayerTeamSeason, Season, SnapCount, Team)
from source_registry import capture_raw, clear_raw_cache, prime_raw_cache, register_source
from team_identity import normalize_team

NFLVERSE_BASE = "https://github.com/nflverse/nflverse-data/releases/download"


def _team(value):
    """Canonical abbreviation, shared with the ESPN cache importer."""
    return normalize_team(value) or ""


def _int(value):
    try:
        return int(float(value)) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _float(value):
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _date(value):
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _download_csv(url: str) -> Iterable[dict]:
    timeout = float(os.environ.get("EXTERNAL_DATA_TIMEOUT", "60"))
    with requests.get(url, timeout=timeout, stream=True, headers={"User-Agent": "nfl-analytics-hub/1.0"}) as response:
        response.raise_for_status()
        raw = response.content
    if url.endswith(".gz"):
        raw = gzip.decompress(raw)
    yield from csv.DictReader(io.StringIO(raw.decode("utf-8-sig")))


def _source() -> DataSource:
    return register_source(
        "nflverse",
        "nflverse public NFL datasets",
        source_type="http",
        base_url="https://github.com/nflverse/nflverse-data/releases",
        license_name="CC-BY-4.0 and upstream source terms",
        attribution="Data provided through nflverse; preserve dataset-specific attribution.",
        refresh_interval_minutes=60,
        metadata={"provider": "nflverse", "format": "csv"},
    )


def _start_run(dataset: str, season: int | None) -> DataSyncRun:
    run = DataSyncRun(source=f"nflverse:{dataset}", details={"dataset": dataset, "season": season})
    db.session.add(run)
    db.session.flush()
    return run


def _finish(run, source, read, written, error=None):
    run.records_read = read
    run.records_written = written
    run.finished_at = datetime.now(timezone.utc)
    run.status = "failed" if error else "success"
    run.error = str(error) if error else None
    if error:
        source.last_failure_at = run.finished_at
    else:
        source.last_success_at = run.finished_at
    db.session.commit()


def _game_lookup(season: int):
    teams = {t.abbreviation: t for t in db.session.scalars(db.select(Team)).all()}
    games = db.session.scalars(db.select(Game).where(Game.season == season)).all()
    by_matchup = {}
    for g in games:
        home = db.session.get(Team, g.home_team_id)
        away = db.session.get(Team, g.away_team_id)
        if not home or not away:
            continue
        # Key on season type as well as week: a wildcard rematch of a week-1
        # game shares (week, away, home) with it, and in 2024 two such pairs
        # existed, so a week-only key silently pointed regular-season rows at
        # the playoff game.
        by_matchup[(g.season_type, g.week, away.abbreviation, home.abbreviation)] = g
        # nflverse numbers the postseason continuously (19, 20, 21, ...) while
        # the schedule stores POST weeks 1-5. Postseason matchups are unique
        # within a season, so register a week-agnostic key for them.
        if g.season_type == "POST":
            by_matchup[("POST", None, away.abbreviation, home.abbreviation)] = g
    return teams, by_matchup


_POST_TYPES = {"POST", "WC", "DIV", "CON", "CONF", "SB"}


def _resolve_game(games, row, week, away, home):
    """Find the scheduled game a provider row belongs to.

    Feeds label the postseason explicitly, which matters because the regular
    season ran 17 weeks through 2020 and 18 from 2021, so a week number alone
    cannot say which side of the split a row is on.
    """
    kind = str(row.get("season_type") or row.get("game_type") or "").strip().upper()
    if kind in _POST_TYPES:
        return games.get(("POST", None, away, home))
    if kind == "REG":
        return games.get(("REG", week, away, home))
    # No label: postseason week numbering continues past the regular season.
    if week is not None and week > 18:
        return games.get(("POST", None, away, home))
    return games.get(("REG", week, away, home)) or games.get(("POST", None, away, home))


def _play_mapping(row, game_id, sequence, teams) -> dict:
    """Flatten one nflverse play row onto the Play columns."""
    posteam, defteam = teams.get(_team(row.get("posteam"))), teams.get(_team(row.get("defteam")))
    y100 = _int(row.get("yardline_100"))
    return {
        "game_id": game_id,
        "sequence": sequence,
        "drive_id": str(row.get("drive") or "") or None,
        "quarter": _int(row.get("qtr")),
        "clock_seconds": _int(row.get("quarter_seconds_remaining")),
        "offense_team_id": posteam.id if posteam else None,
        "defense_team_id": defteam.id if defteam else None,
        "down": _int(row.get("down")),
        "yards_to_go": _int(row.get("ydstogo")),
        "yard_line": 100 - y100 if y100 is not None else None,
        "play_type": row.get("play_type"),
        "description": row.get("desc"),
        "yards_gained": _float(row.get("yards_gained")),
        "first_down": str(row.get("first_down") or "0") == "1",
        "touchdown": str(row.get("touchdown") or "0") == "1",
        "turnover": str(row.get("interception") or "0") == "1" or str(row.get("fumble_lost") or "0") == "1",
        "expected_points_before": _float(row.get("ep")),
        "expected_points_after": _float(row.get("ep_after")),
        "epa": _float(row.get("epa")),
        "success": str(row.get("success") or "0") == "1",
        "win_probability_before": _float(row.get("wp")),
        "win_probability_after": _float(row.get("vegas_wp")),
        "wpa": _float(row.get("wpa")),
        "personnel": row.get("offense_personnel"),
        "formation": row.get("offense_formation"),
        # The full 370-column row is deliberately not duplicated here: it is
        # ~10.6 KB per play and source_registry already stores it once, with the
        # hash this importer uses to spot revisions. Writing it twice cost about
        # a gigabyte per season for a column nothing reads.
    }


def sync_pbp(season: int) -> dict:
    """Import nflverse play-by-play for a season directly into the Play table.

    A season is ~50k plays and the widest feed there is, so this follows the
    same shape as the other bulk imports: prime the provenance hashes, hold only
    the play keys already stored, and insert in bounded batches. A play whose
    payload is unchanged is left alone, which makes re-runs cheap; nflverse does
    revise the current season, so a changed payload still rewrites the row.
    """
    from db_models import Play
    source = _source(); run = _start_run("pbp", season); prime_raw_cache(source, "play")
    read = written = skipped = unchanged = updated = 0
    url = f"{NFLVERSE_BASE}/pbp/play_by_play_{season}.csv.gz"
    try:
        teams, games = _game_lookup(season)
        season_game_ids = {g.id for g in games.values()}
        stored = {
            ext: pid for pid, ext in db.session.execute(
                db.select(Play.id, Play.external_id).where(Play.game_id.in_(season_game_ids))
            ).all()
        } if season_game_ids else {}
        pending: list[dict] = []
        revisions: list[dict] = []
        now = datetime.now(timezone.utc)

        def _flush():
            if pending:
                db.session.bulk_insert_mappings(Play, pending); pending.clear()
            if revisions:
                db.session.bulk_update_mappings(Play, revisions); revisions.clear()
            db.session.commit()

        for row in _download_csv(url):
            read += 1
            week = _int(row.get("week"))
            away_abbr, home_abbr = _team(row.get("away_team")), _team(row.get("home_team"))
            game = _resolve_game(games, row, week, away_abbr, home_abbr)
            play_id = str(row.get("play_id") or "")
            if not game or not play_id:
                skipped += 1; continue
            external_id = f"nflverse:{row.get('game_id')}:{play_id}"
            changed = capture_raw(source, "play", external_id, row, season=season, week=week)
            if external_id in stored:
                existing_id = stored[external_id]
                # None means this run already queued the insert, so a repeated
                # play id in the feed must not be inserted a second time.
                if existing_id is None or not changed:
                    unchanged += 1; continue
                revisions.append({"id": existing_id, "updated_at": now,
                                  **_play_mapping(row, game.id, _int(play_id) or read, teams)})
                updated += 1
            else:
                stored[external_id] = None
                pending.append({"external_id": external_id, "created_at": now, "updated_at": now,
                                **_play_mapping(row, game.id, _int(play_id) or read, teams)})
                written += 1
            if len(pending) + len(revisions) >= 10000:
                _flush()
        _flush(); _finish(run, source, read, written); clear_raw_cache()
        return {"provider": "nflverse", "dataset": "pbp", "season": season, "read": read,
                "written": written, "updated": updated, "unchanged": unchanged,
                "skipped": skipped, "url": url}
    except Exception as exc:
        db.session.rollback(); clear_raw_cache()
        source = _source(); run = db.session.get(DataSyncRun, run.id) or _start_run("pbp", season)
        _finish(run, source, read, written, exc)
        raise


def _load_nflreadpy(dataset: str, season: int):
    try:
        import nflreadpy as nfl
    except ImportError as exc:
        raise RuntimeError("nflreadpy is required for rosters, injuries, depth charts, and snap counts") from exc
    fn = {
        "rosters": nfl.load_rosters_weekly,
        "injuries": nfl.load_injuries,
        "depth_charts": nfl.load_depth_charts,
        "snap_counts": nfl.load_snap_counts,
        "player_stats": nfl.load_player_stats,
    }[dataset]
    frame = fn([season])
    # Materializing a whole season at once costs roughly a gigabyte for depth
    # charts. Yield slices instead; every caller iterates exactly once.
    if hasattr(frame, "iter_slices"):
        return _iter_frame_rows(frame)
    return frame.to_dicts() if hasattr(frame, "to_dicts") else frame.to_dict("records")


def _iter_frame_rows(frame, chunk: int = 20000):
    for slice_ in frame.iter_slices(chunk):
        yield from slice_.to_dicts()


# _ensure_player runs once per source row; a season of depth charts is half a
# million of them. Priming this index replaces the per-row lookup with a dict
# hit. Entries are live session objects, so writes through them still flush.
_players_by_ext: dict[str, Player] | None = None


def prime_player_index() -> int:
    """Load every player once so bulk imports stop querying per row."""
    global _players_by_ext
    _players_by_ext = {p.external_id: p for p in db.session.scalars(db.select(Player)).all()}
    return len(_players_by_ext)


def clear_player_index() -> None:
    global _players_by_ext
    _players_by_ext = None


def _ensure_player(row) -> Player | None:
    ext = str(row.get("gsis_id") or row.get("player_id") or row.get("nflverse_id") or "").strip()
    name = str(
        row.get("full_name") or row.get("player_display_name")
        or row.get("player_name") or row.get("name") or ""
    ).strip()
    if not ext or not name:
        return None
    if _players_by_ext is not None:
        player = _players_by_ext.get(ext)
    else:
        player = db.session.scalar(db.select(Player).where(Player.external_id == ext))
    if not player:
        player = Player(external_id=ext, full_name=name)
        db.session.add(player); db.session.flush()
        if _players_by_ext is not None:
            _players_by_ext[ext] = player
    player.full_name = name; player.position = row.get("position") or player.position
    pfr = str(row.get("pfr_id") or "").strip()
    if pfr and not player.pfr_id:
        player.pfr_id = pfr
    espn = str(row.get("espn_id") or "").strip()
    if espn and not player.espn_id:
        player.espn_id = espn
    return player


def _normalized_name(value) -> str:
    """Fold a display name for cross-source matching (punctuation, suffixes)."""
    text = re.sub(r"[.'\u2019-]", "", str(value or "").lower().strip())
    text = re.sub(r"\s+(jr|sr|ii|iii|iv|v)$", "", text)
    return re.sub(r"\s+", " ", text)


def sync_rosters(season: int) -> dict:
    source = _source(); run = _start_run("rosters", season); prime_raw_cache(source, "roster"); prime_player_index(); rows = _load_nflreadpy("rosters", season)
    teams = {t.abbreviation: t for t in db.session.scalars(db.select(Team)).all()}; read = written = 0
    try:
        if not db.session.get(Season, season): db.session.add(Season(year=season))
        for row in rows:
            read += 1; player = _ensure_player(row); team = teams.get(_team(row.get("team")))
            if not player or not team: continue
            link = db.session.scalar(db.select(PlayerTeamSeason).where(PlayerTeamSeason.player_id == player.id, PlayerTeamSeason.team_id == team.id, PlayerTeamSeason.season == season))
            if not link:
                link = PlayerTeamSeason(player_id=player.id, team_id=team.id, season=season); db.session.add(link)
            link.jersey_number = str(row.get("jersey_number") or "") or None; link.depth_position = row.get("depth_chart_position"); link.status = row.get("status")
            capture_raw(source, "roster", f"{season}:{team.abbreviation}:{player.external_id}:{row.get('week')}", row, season=season, week=_int(row.get("week")))
            written += 1
        db.session.commit(); _finish(run, source, read, written); clear_raw_cache(); clear_player_index(); return {"dataset": "rosters", "season": season, "read": read, "written": written}
    except Exception as exc:
        db.session.rollback(); clear_raw_cache(); clear_player_index(); _finish(run, source, read, written, exc); raise


def sync_injuries(season: int) -> dict:
    """Weekly injury reports.

    The feed publishes no report date, so the grain is one row per player, per
    team, per week. Injury descriptions arrive split across a game-report set
    (report_*) and a practice-report set (practice_*); the game report wins and
    the practice report fills in when a player carries no game designation.
    """
    source = _source(); run = _start_run("injuries", season); prime_raw_cache(source, "injury"); prime_player_index(); rows = _load_nflreadpy("injuries", season)
    teams = {t.abbreviation: t for t in db.session.scalars(db.select(Team)).all()}
    read = written = skipped = 0
    try:
        for row in rows:
            read += 1
            player = _ensure_player(row); team = teams.get(_team(row.get("team"))); week = _int(row.get("week"))
            if not player or not team or week is None:
                skipped += 1; continue
            item = db.session.scalar(db.select(InjuryReport).where(InjuryReport.player_id == player.id, InjuryReport.team_id == team.id, InjuryReport.season == season, InjuryReport.week == week))
            if not item:
                item = InjuryReport(player_id=player.id, team_id=team.id, season=season, week=week); db.session.add(item)
            item.report_date = _date(row.get("date_modified") or row.get("report_date"))
            item.game_status = row.get("report_status") or row.get("game_status")
            item.practice_status = row.get("practice_status")
            item.primary_injury = row.get("report_primary_injury") or row.get("practice_primary_injury") or row.get("primary_injury")
            item.secondary_injury = row.get("report_secondary_injury") or row.get("practice_secondary_injury") or row.get("secondary_injury")
            item.raw_payload = row
            capture_raw(source, "injury", f"{season}:{week}:{team.abbreviation}:{player.external_id}", row, season=season, week=week)
            written += 1
        db.session.commit(); _finish(run, source, read, written); clear_raw_cache(); clear_player_index()
        return {"dataset": "injuries", "season": season, "read": read, "written": written, "skipped": skipped}
    except Exception as exc:
        db.session.rollback(); clear_raw_cache(); clear_player_index(); _finish(run, source, read, written, exc); raise


def sync_depth_charts(season: int) -> dict:
    source = _source(); run = _start_run("depth_charts", season); prime_raw_cache(source, "depth_chart"); prime_player_index(); rows = _load_nflreadpy("depth_charts", season)
    teams = {t.abbreviation: t for t in db.session.scalars(db.select(Team)).all()}
    read = written = skipped = unchanged = 0
    # A season is ~550k rows. Hold only the natural keys already stored, never
    # the mapped objects, and insert new rows in bounded batches so the session
    # identity map cannot grow with the feed.
    existing = {
        tuple(r) for r in db.session.execute(
            db.select(DepthChartEntry.player_id, DepthChartEntry.team_id,
                      DepthChartEntry.week, DepthChartEntry.chart_date,
                      DepthChartEntry.depth_position)
            .where(DepthChartEntry.season == season)
        ).all()
    }
    pending: list[dict] = []
    now = datetime.now(timezone.utc)

    def _flush_pending():
        if pending:
            db.session.bulk_insert_mappings(DepthChartEntry, pending)
            pending.clear()
        db.session.commit()

    try:
        for row in rows:
            read += 1; player = _ensure_player(row)
            # Weekly charts name the club `club_code`; dated snapshots use `team`.
            team = teams.get(_team(row.get("team") or row.get("club_code")))
            chart_date = _date(row.get("dt") or row.get("date") or row.get("chart_date"))
            week = _int(row.get("week"))
            # One of the two grains must identify the row.
            if not player or not team or (chart_date is None and week is None):
                skipped += 1; continue
            depth_pos = row.get("pos_abb") or row.get("depth_position") or row.get("position")
            entry_key = (player.id, team.id, week, chart_date, depth_pos)
            # Dated snapshots are append-only: a key already stored is the same
            # observation, so re-runs skip it instead of rewriting the row.
            if entry_key in existing:
                unchanged += 1; continue
            existing.add(entry_key)
            pending.append({
                "player_id": player.id, "team_id": team.id, "season": season,
                "week": week, "chart_date": chart_date, "position": row.get("position"),
                "depth_position": depth_pos,
                # `pos_rank` on dated snapshots, `depth_team` on weekly charts.
                "depth_rank": _int(row.get("pos_rank") or row.get("depth_rank") or row.get("depth_team")),
                "source_key": "nflverse", "raw_payload": row,
                "created_at": now, "updated_at": now,
            })
            capture_raw(source, "depth_chart", f"{team.abbreviation}:{player.external_id}:{chart_date}:{depth_pos}", row, season=season, week=week); written += 1
            if len(pending) >= 20000:
                _flush_pending()
        _flush_pending(); _finish(run, source, read, written); clear_raw_cache(); clear_player_index()
        return {"dataset": "depth_charts", "season": season, "read": read, "written": written, "skipped": skipped, "unchanged": unchanged}
    except Exception as exc:
        db.session.rollback(); clear_raw_cache(); clear_player_index(); _finish(run, source, read, written, exc); raise


def _snap_player_index() -> tuple[dict, dict]:
    """Resolve snap-count rows onto players loaded from the roster feed.

    Snap counts identify players only by Pro-Football-Reference id, so match on
    players.pfr_id first and fall back to a folded name plus current team. The
    residue is players whose team changed mid-season; they are skipped rather
    than matched on name alone, which would collide across the league.
    """
    by_pfr, by_name = {}, {}
    rows = db.session.execute(
        db.select(Player.id, Player.pfr_id, Player.full_name, Team.abbreviation)
        .select_from(Player)
        .join(PlayerTeamSeason, PlayerTeamSeason.player_id == Player.id)
        .join(Team, Team.id == PlayerTeamSeason.team_id)
    ).all()
    for player_id, pfr_id, full_name, abbr in rows:
        if pfr_id:
            by_pfr.setdefault(pfr_id, player_id)
        by_name.setdefault((_normalized_name(full_name), abbr), player_id)
    return by_pfr, by_name


def _snap_game_key(row) -> tuple | None:
    """nflverse game ids read {season}_{week}_{away}_{home}; the feed has no
    home/away flag of its own."""
    parts = str(row.get("game_id") or "").split("_")
    if len(parts) != 4:
        return None
    week = _int(parts[1])
    return None if week is None else (week, _team(parts[2]), _team(parts[3]))


def sync_snap_counts(season: int) -> dict:
    source = _source(); run = _start_run("snap_counts", season); prime_raw_cache(source, "snap_count"); rows = _load_nflreadpy("snap_counts", season)
    teams, games = _game_lookup(season); read = written = skipped = 0
    by_pfr, by_name = _snap_player_index()
    try:
        for row in rows:
            read += 1
            team = teams.get(_team(row.get("team"))); week = _int(row.get("week"))
            key = _snap_game_key(row)
            game = _resolve_game(games, row, key[0], key[1], key[2]) if key else None
            player_id = by_pfr.get(str(row.get("pfr_player_id") or "").strip())
            if player_id is None and team is not None:
                player_id = by_name.get((_normalized_name(row.get("player")), team.abbreviation))
            player = db.session.get(Player, player_id) if player_id else None
            if not player or not team or not game or week is None:
                skipped += 1; continue
            item = db.session.scalar(db.select(SnapCount).where(SnapCount.game_id == game.id, SnapCount.player_id == player.id))
            if not item: item = SnapCount(game_id=game.id, player_id=player.id, team_id=team.id, season=season, week=week); db.session.add(item)
            item.offense_snaps = _int(row.get("offense_snaps")) or 0; item.offense_pct = _float(row.get("offense_pct"))
            item.defense_snaps = _int(row.get("defense_snaps")) or 0; item.defense_pct = _float(row.get("defense_pct"))
            item.special_teams_snaps = _int(row.get("st_snaps") or row.get("special_teams_snaps")) or 0; item.special_teams_pct = _float(row.get("st_pct") or row.get("special_teams_pct")); item.raw_payload = row
            capture_raw(source, "snap_count", f"{game.external_id}:{player.external_id}", row, season=season, week=week); written += 1
        db.session.commit(); _finish(run, source, read, written); clear_raw_cache(); clear_player_index()
        return {"dataset": "snap_counts", "season": season, "read": read, "written": written, "skipped": skipped}
    except Exception as exc:
        db.session.rollback(); clear_raw_cache(); clear_player_index(); _finish(run, source, read, written, exc); raise


# nflverse names four of the stat columns differently from the warehouse, which
# follows the shape the ESPN boxscore cache established.
_PLAYER_STAT_FIELDS = {
    "completions": "completions",
    "attempts": "attempts",
    "passing_yards": "passing_yards",
    "passing_tds": "passing_tds",
    "interceptions": "passing_interceptions",
    "sacks": "sacks_suffered",
    "carries": "carries",
    "rushing_yards": "rushing_yards",
    "rushing_tds": "rushing_tds",
    "receptions": "receptions",
    "targets": "targets",
    "receiving_yards": "receiving_yards",
    "receiving_tds": "receiving_tds",
    "fumbles_lost": "fumbles_lost_total",
}


def sync_player_stats(season: int) -> dict:
    """Import weekly player lines, the one dataset the disk cache never covered.

    player_game_stats has only ever been filled from the ESPN boxscore cache
    under data/, which ships a single season. Rows already stored are left
    alone, so ESPN stays authoritative wherever it has already written.
    """
    from db_models import PlayerGameStat

    source = _source(); run = _start_run("player_stats", season)
    prime_raw_cache(source, "player_game_stat"); prime_player_index()
    rows = _load_nflreadpy("player_stats", season)
    teams, games = _game_lookup(season)
    read = written = skipped = unchanged = 0
    existing = {
        tuple(r) for r in db.session.execute(
            db.select(PlayerGameStat.game_id, PlayerGameStat.player_id)
            .join(Game, Game.id == PlayerGameStat.game_id)
            .where(Game.season == season)
        ).all()
    }
    pending: list[dict] = []
    now = datetime.now(timezone.utc)

    def _flush():
        if pending:
            db.session.bulk_insert_mappings(PlayerGameStat, pending); pending.clear()
        db.session.commit()

    try:
        for row in rows:
            read += 1
            player = _ensure_player(row)
            key = _snap_game_key(row)  # nflverse game ids share one format
            game = _resolve_game(games, row, key[0], key[1], key[2]) if key else None
            team = teams.get(_team(row.get("team")))
            opponent = teams.get(_team(row.get("opponent_team")))
            if not player or not game or not team or not opponent:
                skipped += 1; continue
            if (game.id, player.id) in existing:
                unchanged += 1; continue
            existing.add((game.id, player.id))
            mapping = {
                "game_id": game.id, "player_id": player.id, "team_id": team.id,
                "opponent_id": opponent.id, "position": row.get("position"),
                # key[2] is the home side of the nflverse game id.
                "home": team.abbreviation == key[2],
                "created_at": now, "updated_at": now,
            }
            for column, field in _PLAYER_STAT_FIELDS.items():
                mapping[column] = _float(row.get(field)) or 0
            pending.append(mapping)
            capture_raw(source, "player_game_stat", f"{game.external_id}:{player.external_id}",
                        row, season=season, week=_int(row.get("week")))
            written += 1
            if len(pending) >= 20000:
                _flush()
        _flush(); _finish(run, source, read, written); clear_raw_cache(); clear_player_index()
        return {"dataset": "player_stats", "season": season, "read": read,
                "written": written, "unchanged": unchanged, "skipped": skipped}
    except Exception as exc:
        db.session.rollback(); clear_raw_cache(); clear_player_index()
        _finish(run, source, read, written, exc); raise


def sync_external(season: int, datasets: list[str]) -> dict:
    funcs = {"pbp": sync_pbp, "rosters": sync_rosters, "injuries": sync_injuries, "depth_charts": sync_depth_charts, "snap_counts": sync_snap_counts, "player_stats": sync_player_stats}
    result = {}
    for dataset in datasets:
        if dataset not in funcs: raise ValueError(f"unsupported dataset: {dataset}")
        result[dataset] = funcs[dataset](season)
    return result
