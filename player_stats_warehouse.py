"""P3.2 player-stat population and projection-readiness verification.

The production app already ships a complete 2025 ESPN-compatible player-week
baseline and can build 2026 completed-game lines from ESPN's public boxscores.
P3.2 imports both into the normalized warehouse, rebuilds player-season
aggregates, and verifies that the current 2026 roster has a useful historical
projection pool.

No sportsbook, Odds API, or commercial-provider call is made here.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import distinct, func, select

import nfl_data
from analytics_warehouse import rebuild_player_seasons
from data_ingestion import import_player_week, import_schedule
from database import db
from db_models import DataSyncRun, Game, PlayerGameStat, PlayerSeasonStat
from projection_readiness import projection_pool_snapshot
from source_registry import clear_raw_cache, prime_raw_cache, register_source


def _seed_dir() -> Path:
    configured = os.environ.get("NFL_SEED_DATA_DIR") or os.environ.get("SEED_DATA_DIR")
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parent / "data"


def _runtime_player_week_path(season: int) -> Path:
    return Path(nfl_data.DATA_DIR) / f"player_week_{season}.csv"


def _source(key: str, name: str, *, source_type: str, base_url: str | None = None):
    source = register_source(
        key,
        name,
        source_type=source_type,
        base_url=base_url,
        license_name="Source-dependent public NFL data terms",
        attribution="Player statistics retained with source provenance for model reproducibility.",
        refresh_interval_minutes=60 if source_type == "http" else 1440,
        metadata={"phase": "P3.2", "dataset": "player_game_stats"},
    )
    db.session.commit()
    return source


def _import_seed_schedule(season: int, source) -> dict[str, Any]:
    path = _seed_dir() / f"schedule_{season}.json"
    if not path.exists():
        raise RuntimeError(f"missing bundled schedule baseline: {path.name}")
    return import_schedule(path, source=source)


def _import_with_provenance_cache(path: Path, source) -> dict[str, Any]:
    """Import a stat CSV without one provenance existence query per row."""
    prime_raw_cache(source, "player_game_stat")
    try:
        return import_player_week(path, source=source)
    finally:
        clear_raw_cache()


def _import_baseline_stats(season: int, source) -> dict[str, Any]:
    path = _seed_dir() / f"player_week_{season}.csv"
    if not path.exists():
        raise RuntimeError(f"missing bundled player-stat baseline: {path.name}")
    return _import_with_provenance_cache(path, source)


def refresh_current_stats(season: int, source) -> dict[str, Any]:
    """Refresh schedule + completed current-season ESPN player boxscores.

    Fly SSH may select a process without the web volume. Refreshing the public
    schedule inside this process means completed-game discovery never depends
    on whichever local schedule snapshot happens to exist on that machine.
    The durable output is the normalized database, not this temporary cache.
    """
    games = nfl_data.get_schedule(season, refresh=True)
    completed_games = sum(1 for game in games if game.get("completed"))
    rows = nfl_data.get_player_week_stats(season, refresh=True)
    path = _runtime_player_week_path(season)
    if rows and not path.exists():
        raise RuntimeError("current player rows were built but the runtime cache file is missing")
    imported = _import_with_provenance_cache(path, source) if path.exists() else {
        "read": 0,
        "written": 0,
        "skipped": 0,
    }
    return {
        "cache_rows": len(rows),
        "cache_file": path.name,
        "completed_games_discovered": completed_games,
        **imported,
    }


def _season_fact_snapshot(season: int) -> dict[str, Any]:
    stat_rows = int(
        db.session.scalar(
            select(func.count())
            .select_from(PlayerGameStat)
            .join(Game, Game.id == PlayerGameStat.game_id)
            .where(Game.season == season)
        )
        or 0
    )
    players = int(
        db.session.scalar(
            select(func.count(distinct(PlayerGameStat.player_id)))
            .select_from(PlayerGameStat)
            .join(Game, Game.id == PlayerGameStat.game_id)
            .where(Game.season == season)
        )
        or 0
    )
    games = int(
        db.session.scalar(
            select(func.count(distinct(PlayerGameStat.game_id)))
            .select_from(PlayerGameStat)
            .join(Game, Game.id == PlayerGameStat.game_id)
            .where(Game.season == season)
        )
        or 0
    )
    regular_weeks = [
        int(week)
        for week in db.session.scalars(
            select(distinct(Game.week))
            .select_from(Game)
            .join(PlayerGameStat, PlayerGameStat.game_id == Game.id)
            .where(Game.season == season, Game.season_type == "REG")
            .order_by(Game.week)
        ).all()
        if week is not None
    ]
    season_rows = int(
        db.session.scalar(
            select(func.count()).select_from(PlayerSeasonStat).where(PlayerSeasonStat.season == season)
        )
        or 0
    )
    return {
        "season": season,
        "player_game_rows": stat_rows,
        "players_with_stats": players,
        "games_with_player_stats": games,
        "regular_weeks": regular_weeks,
        "player_season_rows": season_rows,
    }


def player_stats_readiness_snapshot(target_season: int, baseline_season: int) -> dict[str, Any]:
    baseline = _season_fact_snapshot(baseline_season)
    current = _season_fact_snapshot(target_season)
    projection = projection_pool_snapshot(target_season)

    min_baseline_rows = max(int(os.environ.get("P32_MIN_BASELINE_ROWS", "3000")), 1)
    min_baseline_players = max(int(os.environ.get("P32_MIN_BASELINE_PLAYERS", "400")), 1)
    min_current_rows = max(int(os.environ.get("P32_MIN_CURRENT_ROWS", "100")), 0)
    min_ready_players = max(int(os.environ.get("P32_MIN_READY_SKILL_PLAYERS", "250")), 1)
    min_ready_coverage = float(os.environ.get("P32_MIN_READY_SKILL_COVERAGE", "0.50"))
    min_season_rows = max(int(os.environ.get("P32_MIN_PLAYER_SEASON_ROWS", "300")), 1)

    gates = {
        "historical_game_evidence": baseline["player_game_rows"] >= min_baseline_rows,
        "historical_player_coverage": baseline["players_with_stats"] >= min_baseline_players,
        "current_completed_game_evidence": current["player_game_rows"] >= min_current_rows,
        "player_season_aggregates": baseline["player_season_rows"] >= min_season_rows,
        "projection_ready_player_pool": projection["projection_ready_skill_players"] >= min_ready_players,
        "projection_ready_coverage": (
            projection["projection_ready_returning_skill_coverage"] >= min_ready_coverage
        ),
    }
    return {
        "target_season": target_season,
        "baseline_season": baseline_season,
        "baseline": baseline,
        "current": current,
        "projection": projection,
        "thresholds": {
            "minimum_baseline_rows": min_baseline_rows,
            "minimum_baseline_players": min_baseline_players,
            "minimum_current_rows": min_current_rows,
            "minimum_ready_skill_players": min_ready_players,
            "minimum_ready_skill_coverage": min_ready_coverage,
            "projection_ready_coverage_denominator": "returning_current_skill_players",
            "minimum_player_season_rows": min_season_rows,
        },
        "gates": gates,
        "ok": all(gates.values()),
    }


def populate_player_stats(target_season: int = 2026, baseline_season: int = 2025) -> dict[str, Any]:
    """Populate baseline/current facts and return a sanitized readiness result."""
    run = DataSyncRun(
        source="p3.2-player-stats",
        details={"target_season": target_season, "baseline_season": baseline_season},
    )
    db.session.add(run)
    db.session.commit()

    seed_source = _source(
        "p32-historical-baseline",
        "P3.2 bundled historical player baseline",
        source_type="file",
    )
    espn_source = _source(
        "espn-player-stats",
        "ESPN public completed-game player boxscores",
        source_type="http",
        base_url=nfl_data.ESPN_BASE,
    )

    try:
        schedules = {
            str(baseline_season): _import_seed_schedule(baseline_season, seed_source),
            str(target_season): _import_seed_schedule(target_season, seed_source),
        }
        baseline = _import_baseline_stats(baseline_season, seed_source)
        current = refresh_current_stats(target_season, espn_source)
        rebuilt = {
            str(baseline_season): rebuild_player_seasons(baseline_season),
            str(target_season): rebuild_player_seasons(target_season),
        }
        readiness = player_stats_readiness_snapshot(target_season, baseline_season)
        run.status = "completed" if readiness["ok"] else "failed"
        run.records_read = int(baseline.get("read") or 0) + int(current.get("read") or 0)
        run.records_written = int(baseline.get("written") or 0) + int(current.get("written") or 0)
        run.details = {
            "schedules": schedules,
            "baseline": baseline,
            "current": current,
            "player_seasons_rebuilt": rebuilt,
            "readiness": readiness,
        }
        run.finished_at = datetime.now(timezone.utc)
        db.session.commit()
        return {
            "mode": "player-stats-sync",
            "phase": "P3.2",
            "target_season": target_season,
            "baseline_season": baseline_season,
            "baseline_import": baseline,
            "current_import": current,
            "player_seasons_rebuilt": rebuilt,
            "readiness": readiness,
            "ok": bool(readiness["ok"]),
        }
    except Exception as exc:
        db.session.rollback()
        persisted = db.session.get(DataSyncRun, run.id)
        if persisted:
            persisted.status = "failed"
            persisted.error = str(exc)
            persisted.finished_at = datetime.now(timezone.utc)
            db.session.commit()
        raise
