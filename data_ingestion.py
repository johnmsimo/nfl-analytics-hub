"""Idempotent imports from the app's existing ESPN/nflverse-compatible cache."""
from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select

from database import db
from db_models import (
    Coach,
    CoachingAssignment,
    DataSyncRun,
    Game,
    PlayerGameStat,
    PlayerTeamSeason,
    Season,
    Team,
)
from player_identity import resolve_player
from source_registry import capture_raw, register_source
from team_identity import normalize_team

STAT_FIELDS = [
    "completions", "attempts", "passing_yards", "passing_tds", "interceptions",
    "sacks", "carries", "rushing_yards", "rushing_tds", "receptions", "targets",
    "receiving_yards", "receiving_tds", "fumbles_lost",
]


def _dt(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _num(value, integer=False):
    if value in (None, ""):
        return 0 if integer else 0.0
    try:
        return int(float(value)) if integer else float(value)
    except (TypeError, ValueError):
        return 0 if integer else 0.0


def _upsert_team(abbr, name=None, external_id=None):
    """Upsert a canonical team while safely adopting pre-normalization rows.

    Production may still contain ESPN-era rows such as ``JAX``/``WSH`` from
    before team normalization was introduced. Those rows already own ESPN's
    unique ``external_id``. Looking up only the new canonical abbreviation and
    then inserting ``JAC``/``WAS`` would therefore raise an integrity error.

    When the canonical abbreviation is missing, reuse the row that already owns
    the provider id if its legacy abbreviation normalizes to the same franchise.
    Renaming that row in place preserves every foreign-key relationship.
    """
    canonical = normalize_team(abbr)
    if not canonical:
        return None

    external_key = None
    if external_id not in (None, ""):
        external_key = str(external_id).strip() or None

    team = db.session.scalar(select(Team).where(Team.abbreviation == canonical))
    external_owner = None
    if external_key:
        external_owner = db.session.scalar(select(Team).where(Team.external_id == external_key))

    if not team and external_owner:
        if normalize_team(external_owner.abbreviation) == canonical:
            external_owner.abbreviation = canonical
            team = external_owner
            db.session.flush()

    if not team:
        team = Team(abbreviation=canonical, name=name or canonical, external_id=external_key)
        db.session.add(team)
        db.session.flush()
    else:
        if name:
            team.name = name
        # Never steal a unique provider id from another row. A legacy alias
        # matching this franchise is adopted above when the canonical row does
        # not already exist; split rows can then be reconciled explicitly
        # without making routine cached-data sync fail.
        if external_key and (external_owner is None or external_owner.id == team.id):
            team.external_id = external_key
    return team


def import_schedule(path: str | Path, source=None) -> dict:
    path = Path(path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    games = payload.get("games", payload if isinstance(payload, list) else [])
    written = 0
    raw_written = 0
    for row in games:
        if source and row.get("game_id"):
            raw_written += int(capture_raw(
                source,
                "game",
                str(row["game_id"]),
                row,
                season=int(row.get("season") or 0) or None,
                week=int(row["week"]) if str(row.get("week", "")).strip() else None,
            ))
        season_year = int(row["season"])
        season = db.session.get(Season, season_year)
        if not season:
            season = Season(year=season_year)
            db.session.add(season)
        home = _upsert_team(row.get("home_team"), row.get("home_name"), row.get("home_id"))
        away = _upsert_team(row.get("away_team"), row.get("away_name"), row.get("away_id"))
        if not home or not away or home.id == away.id:
            continue
        game = db.session.scalar(select(Game).where(Game.external_id == str(row["game_id"])))
        if not game:
            game = Game(external_id=str(row["game_id"]), season=season_year,
                        season_type=row.get("season_type", "REG"), week=int(row.get("week", 0)),
                        home_team_id=home.id, away_team_id=away.id)
            db.session.add(game)
        game.kickoff_at = _dt(row.get("date"))
        game.venue = row.get("venue")
        game.state = row.get("state")
        game.status_detail = row.get("status_detail")
        game.completed = bool(row.get("completed"))
        game.home_score = row.get("home_score")
        game.away_score = row.get("away_score")
        written += 1
    db.session.commit()
    return {"read": len(games), "written": written, "raw_versions": raw_written}


def import_player_week(path: str | Path, source=None) -> dict:
    path = Path(path)
    read = written = skipped = 0
    with path.open("r", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            read += 1
            if source and row.get("player_id") and row.get("game_id"):
                capture_raw(source, "player_game_stat", f"{row.get('game_id')}:{row.get('player_id')}", row, season=int(row.get("season") or 0) or None, week=int(row.get("week") or 0) or None)
            game = db.session.scalar(select(Game).where(Game.external_id == str(row.get("game_id"))))
            team = _upsert_team(row.get("team"))
            opponent = _upsert_team(row.get("opponent"))
            if not game or not team or not opponent or not row.get("player_id"):
                skipped += 1
                continue
            season_year = int(row["season"])
            if not db.session.get(Season, season_year):
                db.session.add(Season(year=season_year))
                db.session.flush()

            # ESPN ids live in their own namespace. The source-scoped resolver
            # can attach this id to a roster player by a trusted id bridge or a
            # unique same-team name match without confusing another provider's
            # numeric id for ESPN's.
            espn_id = str(row["player_id"])
            player = resolve_player(
                {"espn": espn_id},
                full_name=row.get("player_name") or espn_id,
                position=row.get("position"),
                team_id=team.id,
                season=season_year,
            )
            if not player:
                skipped += 1
                continue
            membership = db.session.scalar(select(PlayerTeamSeason).where(
                PlayerTeamSeason.player_id == player.id,
                PlayerTeamSeason.team_id == team.id,
                PlayerTeamSeason.season == season_year,
            ))
            if not membership:
                db.session.add(PlayerTeamSeason(player_id=player.id, team_id=team.id, season=season_year))

            stat = db.session.scalar(select(PlayerGameStat).where(
                PlayerGameStat.game_id == game.id, PlayerGameStat.player_id == player.id))
            if not stat:
                stat = PlayerGameStat(game_id=game.id, player_id=player.id,
                                      team_id=team.id, opponent_id=opponent.id,
                                      position=row.get("position"),
                                      home=str(row.get("home", "")).lower() in {"1", "true", "yes"})
                db.session.add(stat)
            for field in STAT_FIELDS:
                setattr(stat, field, _num(row.get(field), integer=field in {"completions", "attempts"}))
            written += 1
            if written % 1000 == 0:
                db.session.commit()
    db.session.commit()
    return {"read": read, "written": written, "skipped": skipped}


def sync_cached_data(data_dir: str | Path) -> dict:
    data_dir = Path(data_dir)
    run = DataSyncRun(source="local-cache")
    db.session.add(run)
    db.session.commit()
    details = {"schedules": [], "player_weeks": []}
    source = register_source(
        "local-cache", "Local NFL data cache", source_type="file",
        license_name="Source-dependent",
        attribution="Cached schedule and player-week files supplied to the application.",
        refresh_interval_minutes=1440,
    )
    db.session.commit()
    try:
        total_read = total_written = 0
        for path in sorted(data_dir.glob("schedule_*.json")):
            result = import_schedule(path, source=source)
            details["schedules"].append({"file": path.name, **result})
            total_read += result["read"]
            total_written += result["written"]
        for path in sorted(data_dir.glob("player_week_*.csv")):
            result = import_player_week(path, source=source)
            details["player_weeks"].append({"file": path.name, **result})
            total_read += result["read"]
            total_written += result["written"]
        coach_path = data_dir / "coaches.csv"
        if coach_path.exists():
            result = import_coaches(coach_path)
            details["coaches"] = {"file": coach_path.name, **result}
            total_read += result["read"]
            total_written += result["written"]
        from analytics_warehouse import rebuild_analytics
        details["analytics"] = rebuild_analytics()
        source.last_success_at = datetime.now(timezone.utc)
        run.status = "completed"
        run.records_read = total_read
        run.records_written = total_written
        run.details = details
    except Exception as exc:
        db.session.rollback()
        run = db.session.get(DataSyncRun, run.id)
        source.last_failure_at = datetime.now(timezone.utc)
        run.status = "failed"
        run.error = str(exc)
        raise
    finally:
        run.finished_at = datetime.now(timezone.utc)
        db.session.commit()
    return {"run_id": run.id, "status": run.status, "records_read": run.records_read,
            "records_written": run.records_written, "details": details}


def _date(value):
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def import_coaches(path: str | Path) -> dict:
    """Import a licensed/manual coach feed using data/coaches_template.csv contract."""
    path = Path(path)
    read = written = skipped = 0
    with path.open("r", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            read += 1
            name = (row.get("full_name") or "").strip()
            team = _upsert_team((row.get("team") or "").strip().upper())
            try:
                season_year = int(row.get("season") or 0)
            except ValueError:
                season_year = 0
            role = (row.get("role") or "").strip()
            if not name or not team or not season_year or not role:
                skipped += 1
                continue
            if not db.session.get(Season, season_year):
                db.session.add(Season(year=season_year))
                db.session.flush()
            external_id = (row.get("external_id") or "").strip() or None
            coach = None
            if external_id:
                coach = db.session.scalar(select(Coach).where(Coach.external_id == external_id))
            if not coach:
                coach = db.session.scalar(select(Coach).where(Coach.full_name == name))
            if not coach:
                coach = Coach(external_id=external_id, full_name=name,
                              birth_date=_date(row.get("birth_date")), active=True)
                db.session.add(coach); db.session.flush()
            else:
                coach.external_id = external_id or coach.external_id
                coach.birth_date = _date(row.get("birth_date")) or coach.birth_date
                coach.active = str(row.get("active", "true")).lower() not in {"0", "false", "no"}
            assignment = db.session.scalar(select(CoachingAssignment).where(
                CoachingAssignment.coach_id == coach.id, CoachingAssignment.team_id == team.id,
                CoachingAssignment.season == season_year, CoachingAssignment.role == role))
            if not assignment:
                assignment = CoachingAssignment(coach_id=coach.id, team_id=team.id, season=season_year, role=role)
                db.session.add(assignment)
            assignment.start_date = _date(row.get("start_date"))
            assignment.end_date = _date(row.get("end_date"))
            written += 1
    db.session.commit()
    return {"read": read, "written": written, "skipped": skipped}
