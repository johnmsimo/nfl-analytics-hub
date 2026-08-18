"""Backfill multiple NFL seasons into the warehouse.

Written to be runnable against a fresh production database, where the only
schedules on disk are the ones committed to the repo. Snap counts, play-by-play
and player lines all resolve through the games table, so a season with no
schedule imports nothing at all — the schedules are fetched and imported first
unless that is turned off.

Play-by-play is excluded from the default datasets. It is the heaviest feed by
an order of magnitude and peaks around 1.3 GB for a single season, which will
not fit the 1 GB web machine; ask for it explicitly, on a machine sized for it.
"""
from __future__ import annotations

import argparse
import json
import os

import nfl_data
from analytics_warehouse import rebuild_analytics
from app import app
from commercial_integrations import sync_commercial
from data_ingestion import sync_cached_data
from data_quality import run_quality_checks
from database import db
from db_models import Game, PlayerTeamSeason
from external_providers import sync_external
from play_by_play import rebuild_advanced_team_stats

# pbp is deliberately absent; see the module docstring.
PUBLIC_DATASETS = ["rosters", "injuries", "depth_charts", "snap_counts", "player_stats"]


def ensure_schedules(start: int, end: int) -> dict:
    """Fetch and import the seasons' schedules so later feeds have games to join."""
    fetched, failed = [], {}
    for season in range(start, end + 1):
        if db.session.scalar(db.select(db.func.count(Game.id)).where(Game.season == season)):
            continue
        try:
            nfl_data.get_schedule(season, refresh=True)
            fetched.append(season)
        except Exception as exc:  # noqa: BLE001
            failed[season] = str(exc)
    imported = sync_cached_data(os.environ.get("NFL_DATA_DIR") or os.path.join(app.root_path, "data"))
    return {"fetched": fetched, "failed": failed,
            "records_written": imported.get("records_written")}


def _already_loaded(season: int) -> bool:
    return bool(db.session.scalar(
        db.select(db.func.count(PlayerTeamSeason.id)).where(PlayerTeamSeason.season == season)))


def run(start: int, end: int, datasets: list[str], commercial: list[str],
        continue_on_error: bool = True, fetch_schedules: bool = True,
        skip_existing: bool = False) -> dict:
    report: dict = {"seasons": {}, "errors": []}
    with app.app_context():
        if fetch_schedules:
            report["schedules"] = ensure_schedules(start, end)
        for season in range(start, end + 1):
            if skip_existing and _already_loaded(season):
                report["seasons"][str(season)] = {"skipped": "already loaded"}
                continue
            entry: dict = {}
            try:
                if datasets:
                    entry["public"] = sync_external(season, datasets)
                if commercial:
                    entry["commercial"] = sync_commercial(season, commercial)
                entry["aggregates"] = rebuild_analytics(season)
                entry["advanced"] = rebuild_advanced_team_stats(season)
                entry["quality"] = run_quality_checks()
            except Exception as exc:  # noqa: BLE001
                db.session.rollback()
                report["errors"].append({"season": season, "error": str(exc)})
                if not continue_on_error:
                    raise
            report["seasons"][str(season)] = entry
            print(json.dumps({"season": season, **entry}, default=str), flush=True)
    return report


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--start", type=int, default=2016)
    p.add_argument("--end", type=int, default=2025)
    p.add_argument("--datasets", default=",".join(PUBLIC_DATASETS),
                   help="comma separated; add 'pbp' only on a machine with ~2 GB free")
    p.add_argument("--commercial", default="")
    p.add_argument("--no-fetch-schedules", action="store_true",
                   help="assume the games are already imported")
    p.add_argument("--skip-existing", action="store_true",
                   help="resume: skip seasons that already carry roster rows")
    p.add_argument("--fail-fast", action="store_true")
    a = p.parse_args()
    result = run(a.start, a.end,
                 [x for x in a.datasets.split(",") if x],
                 [x for x in a.commercial.split(",") if x],
                 continue_on_error=not a.fail_fast,
                 fetch_schedules=not a.no_fetch_schedules,
                 skip_existing=a.skip_existing)
    print(json.dumps(result, indent=2, default=str))
