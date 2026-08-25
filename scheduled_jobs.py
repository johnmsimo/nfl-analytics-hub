"""Optional scheduler with isolated, explicitly enabled data-provider jobs."""
from __future__ import annotations

import os
import threading
from datetime import datetime, timezone

from apscheduler.schedulers.background import BackgroundScheduler

import nfl_data
from analytics_warehouse import rebuild_analytics
from commercial_integrations import sync_commercial
from data_ingestion import sync_cached_data
from data_quality import run_quality_checks
from database import db
from db_models import ScheduledJob
from external_providers import sync_external
from play_by_play import rebuild_advanced_team_stats


_scheduler = None
_PROVIDER_SYNC_LOCK = threading.Lock()
_TRUE_VALUES = {"1", "true", "yes"}


def _provider_job(
    family: str,
    dataset: str,
    name: str,
    minutes: int,
) -> dict:
    env_dataset = dataset.upper()
    return {
        "name": name,
        "minutes": minutes,
        "minutes_env": f"{family.upper()}_{env_dataset}_INTERVAL_MINUTES",
        "enabled_envs": (
            f"ENABLE_{family.upper()}_SYNC",
            f"ENABLE_{family.upper()}_{env_dataset}_SYNC",
        ),
        "provider_family": family,
        "dataset": dataset,
    }


JOBS = {
    "cached-data-sync": {"name": "Cached data sync", "minutes": 60},
    "analytics-rebuild": {"name": "Warehouse aggregate rebuild", "minutes": 60},
    "quality-checks": {"name": "Data quality checks", "minutes": 60},
    "external-rosters-sync": _provider_job(
        "external", "rosters", "nflverse roster sync", 360
    ),
    "external-injuries-sync": _provider_job(
        "external", "injuries", "nflverse injury sync", 120
    ),
    "external-depth-charts-sync": _provider_job(
        "external", "depth_charts", "nflverse depth-chart sync", 1440
    ),
    "external-pbp-sync": _provider_job(
        "external", "pbp", "nflverse play-by-play sync", 360
    ),
    "external-snap-counts-sync": _provider_job(
        "external", "snap_counts", "nflverse snap-count sync", 360
    ),
    "external-player-stats-sync": _provider_job(
        "external", "player_stats", "nflverse player-stat sync", 360
    ),
    "commercial-weather-sync": _provider_job(
        "commercial", "weather", "Weather sync", 30
    ),
    "commercial-odds-sync": _provider_job(
        "commercial", "odds", "Odds sync", 10
    ),
    "commercial-live-games-sync": _provider_job(
        "commercial", "live_games", "Live-game sync", 2
    ),
    "commercial-coaches-sync": _provider_job(
        "commercial", "coaches", "Coaching sync", 1440
    ),
    "commercial-transactions-sync": _provider_job(
        "commercial", "transactions", "Transaction sync", 60
    ),
}


def _env_true(name: str) -> bool:
    return os.environ.get(name, "false").strip().lower() in _TRUE_VALUES


def _job_enabled(key: str) -> bool:
    required = JOBS[key].get("enabled_envs", ())
    return all(_env_true(name) for name in required) if required else True


def _disabled_reason(key: str) -> str:
    missing = [
        name for name in JOBS[key].get("enabled_envs", ())
        if not _env_true(name)
    ]
    return "disabled by " + ", ".join(missing)


def _job_minutes(key: str) -> int:
    cfg = JOBS[key]
    env_name = cfg.get("minutes_env")
    raw = os.environ.get(env_name) if env_name else None
    minutes = int(raw) if raw not in (None, "") else int(cfg["minutes"])
    if minutes < 1:
        raise ValueError(f"{env_name or key} must be at least 1 minute")
    return minutes


def _record(key: str, status: str, error: str | None = None) -> None:
    row = db.session.scalar(db.select(ScheduledJob).where(ScheduledJob.key == key))
    cfg = JOBS[key]
    cron = f"every {_job_minutes(key)} minutes"
    if not row:
        row = ScheduledJob(key=key, name=cfg["name"], cron=cron)
        db.session.add(row)
    else:
        row.name = cfg["name"]
        row.cron = cron
    row.last_status = status
    row.last_error = error
    row.last_finished_at = datetime.now(timezone.utc)
    job = _scheduler.get_job(key) if _scheduler else None
    row.next_run_at = job.next_run_time if job else None
    db.session.commit()


def _register_jobs(app) -> None:
    """Persist scheduler registration so the web process can assess freshness."""
    with app.app_context():
        for key, cfg in JOBS.items():
            row = db.session.scalar(db.select(ScheduledJob).where(ScheduledJob.key == key))
            enabled = _job_enabled(key)
            minutes = _job_minutes(key) if enabled else int(cfg["minutes"])
            if not row:
                row = ScheduledJob(key=key, name=cfg["name"], cron=f"every {minutes} minutes")
                db.session.add(row)
            row.name = cfg["name"]
            row.cron = f"every {minutes} minutes"
            row.enabled = enabled
            job = _scheduler.get_job(key) if _scheduler else None
            row.next_run_at = job.next_run_time if job else None
            if row.enabled and not row.last_status:
                row.last_status = "scheduled"
        db.session.commit()


def _target_season() -> int:
    configured = os.environ.get("EXTERNAL_DATA_SEASON")
    return int(configured) if configured else nfl_data.default_season()


def _target_week(season: int) -> int:
    configured = os.environ.get("EXTERNAL_DATA_WEEK")
    if configured not in (None, ""):
        return int(configured)
    return int(nfl_data.current_week(season)["week"])


def _run_provider_job(key: str) -> None:
    cfg = JOBS[key]
    season = _target_season()
    dataset = cfg["dataset"]

    if cfg["provider_family"] == "external":
        sync_external(season, [dataset])
        if dataset == "pbp":
            rebuild_advanced_team_stats(season)
        return

    week = None if dataset in {"coaches", "transactions"} else _target_week(season)
    sync_commercial(season, [dataset], week)


def run_job(app, key):
    with app.app_context():
        if not _job_enabled(key):
            reason = _disabled_reason(key)
            _record(key, "skipped", reason)
            app.logger.info("scheduled job %s skipped: %s", key, reason)
            return

        row = db.session.scalar(db.select(ScheduledJob).where(ScheduledJob.key == key))
        if row:
            row.last_started_at = datetime.now(timezone.utc)
            db.session.commit()

        is_provider = "provider_family" in JOBS[key]
        if is_provider and not _PROVIDER_SYNC_LOCK.acquire(blocking=False):
            reason = "another provider sync is already running"
            _record(key, "skipped", reason)
            app.logger.warning("scheduled job %s skipped: %s", key, reason)
            return

        try:
            if key == "cached-data-sync":
                sync_cached_data(
                    os.environ.get("NFL_SEED_DATA_DIR")
                    or os.environ.get("SEED_DATA_DIR")
                    or os.path.join(app.root_path, "data")
                )
            elif key == "analytics-rebuild":
                rebuild_analytics(None)
                rebuild_advanced_team_stats(None)
            elif key == "quality-checks":
                run_quality_checks()
            else:
                _run_provider_job(key)
            _record(key, "success")
        except Exception as exc:
            _record(key, "failed", str(exc))
            app.logger.exception("scheduled job %s failed", key)
        finally:
            if is_provider:
                _PROVIDER_SYNC_LOCK.release()


def start_scheduler(app):
    global _scheduler
    if _scheduler or not _env_true("ENABLE_SCHEDULER"):
        return _scheduler

    _scheduler = BackgroundScheduler(timezone="UTC", daemon=True)
    for key in JOBS:
        if not _job_enabled(key):
            app.logger.info(
                "scheduled job %s not registered: %s",
                key,
                _disabled_reason(key),
            )
            continue
        minutes = _job_minutes(key)
        _scheduler.add_job(
            run_job,
            "interval",
            minutes=minutes,
            args=[app, key],
            id=key,
            replace_existing=True,
            coalesce=True,
            max_instances=1,
            misfire_grace_time=max(60, min(minutes * 60, 3600)),
        )
    _scheduler.start()
    _register_jobs(app)
    return _scheduler
