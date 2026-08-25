"""Read-only production freshness reporting for schedules, providers, and jobs."""

from __future__ import annotations

import os
from datetime import UTC, datetime

import provider_health
from database import db
from db_models import DataSource, ScheduledJob
from scheduled_jobs import JOBS, _job_enabled, _job_minutes

_TRUE_VALUES = {"1", "true", "yes"}


def _env_true(name: str) -> bool:
    return os.environ.get(name, "false").strip().lower() in _TRUE_VALUES


def _utc(value: datetime | float | int | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, int | float):
        return datetime.fromtimestamp(float(value), UTC)
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _iso(value: datetime | float | int | None) -> str | None:
    parsed = _utc(value)
    return parsed.isoformat() if parsed else None


def _age_seconds(now: datetime, value: datetime | float | int | None) -> float | None:
    parsed = _utc(value)
    return round(max(0.0, (now - parsed).total_seconds()), 1) if parsed else None


def _rollup(statuses: list[str]) -> str:
    if not statuses or all(status == "unavailable" for status in statuses):
        return "unavailable"
    if any(status == "degraded" for status in statuses):
        return "degraded"
    if any(status == "stale" for status in statuses):
        return "stale"
    if all(status in {"ready", "pending"} for status in statuses):
        return "ready"
    return "partial"


def schedule_freshness(schedule: dict) -> dict:
    """Normalize the schedule snapshot's freshness contract."""
    return {
        "status": schedule.get("freshness_status", "unavailable"),
        "season": schedule.get("season"),
        "fetched_at": schedule.get("fetched_at"),
        "age_seconds": schedule.get("age_seconds"),
        "stale_after_seconds": schedule.get("stale_after_seconds"),
        "total_games": schedule.get("total_games", 0),
        "current_week": schedule.get("current_week"),
        "issues": schedule.get("issues", []),
    }


def provider_freshness(now: datetime) -> dict:
    """Report persistent provider sync age plus current-process HTTP telemetry."""
    multiplier = max(float(os.environ.get("PROVIDER_FRESHNESS_MULTIPLIER", "1.5")), 1.0)
    default_minutes = max(int(os.environ.get("PROVIDER_FRESHNESS_DEFAULT_MINUTES", "1440")), 1)
    sources = []
    try:
        rows = db.session.scalars(
            db.select(DataSource).where(DataSource.enabled.is_(True)).order_by(DataSource.key)
        ).all()
        for row in rows:
            maximum_age = round((row.refresh_interval_minutes or default_minutes) * 60 * multiplier)
            success_age = _age_seconds(now, row.last_success_at)
            success = _utc(row.last_success_at)
            failure = _utc(row.last_failure_at)
            if failure and (not success or failure > success):
                status = "degraded"
            elif success_age is None:
                status = "unavailable"
            elif success_age > maximum_age:
                status = "stale"
            else:
                status = "ready"
            sources.append(
                {
                    "key": row.key,
                    "status": status,
                    "last_success_at": _iso(row.last_success_at),
                    "last_failure_at": _iso(row.last_failure_at),
                    "age_seconds": success_age,
                    "stale_after_seconds": maximum_age,
                }
            )
    except Exception:  # noqa: BLE001 - public health output stays sanitized
        db.session.rollback()
        return {
            "status": "unavailable",
            "sources": [],
            "http_telemetry": [],
            "reason": "Provider freshness records are unavailable.",
        }

    telemetry = []
    telemetry_max_age = max(int(os.environ.get("PROVIDER_HTTP_FRESHNESS_SECONDS", "3600")), 60)
    for key, row in sorted(provider_health.snapshot().items()):
        success_age = _age_seconds(now, row.get("last_success_at"))
        success = _utc(row.get("last_success_at"))
        failure = _utc(row.get("last_failure_at"))
        if failure and (not success or failure > success):
            status = "degraded"
        elif success_age is None:
            status = "unavailable"
        elif success_age > telemetry_max_age:
            status = "stale"
        else:
            status = "ready"
        telemetry.append(
            {
                "key": key,
                "status": status,
                "last_success_at": _iso(row.get("last_success_at")),
                "last_failure_at": _iso(row.get("last_failure_at")),
                "age_seconds": success_age,
                "stale_after_seconds": telemetry_max_age,
                "last_latency_ms": row.get("last_latency_ms"),
            }
        )

    statuses = [item["status"] for item in sources + telemetry]
    return {
        "status": _rollup(statuses),
        "source_count": len(sources),
        "observed_http_provider_count": len(telemetry),
        "sources": sources,
        "http_telemetry": telemetry,
        "reason": None if statuses else "No provider freshness evidence has been recorded.",
    }


def scheduler_freshness(now: datetime) -> dict:
    """Compare enabled job records with their configured execution cadence."""
    expected = _env_true("SCHEDULER_EXPECTED") or _env_true("ENABLE_SCHEDULER")
    if not expected:
        return {
            "status": "disabled",
            "expected": False,
            "enabled_job_count": 0,
            "jobs": [],
            "reason": "Scheduler freshness monitoring is intentionally disabled.",
        }

    enabled_keys = [key for key in JOBS if _job_enabled(key)]
    multiplier = max(float(os.environ.get("SCHEDULER_FRESHNESS_MULTIPLIER", "2.0")), 1.0)
    try:
        rows = db.session.scalars(db.select(ScheduledJob).where(ScheduledJob.key.in_(enabled_keys))).all()
    except Exception:  # noqa: BLE001 - public health output stays sanitized
        db.session.rollback()
        return {
            "status": "unavailable",
            "expected": True,
            "enabled_job_count": len(enabled_keys),
            "jobs": [],
            "reason": "Scheduler freshness records are unavailable.",
        }

    by_key = {row.key: row for row in rows}
    jobs = []
    for key in enabled_keys:
        row = by_key.get(key)
        maximum_age = round(_job_minutes(key) * 60 * multiplier)
        finished_at = row.last_finished_at if row else None
        next_run_at = row.next_run_at if row else None
        finished_age = _age_seconds(now, finished_at)
        if row is None:
            status = "unavailable"
        elif row.last_status == "failed":
            status = "degraded"
        elif finished_age is not None:
            status = "stale" if finished_age > maximum_age else "ready"
        elif next_run_at and now <= (_utc(next_run_at) or now):
            status = "pending"
        elif next_run_at:
            status = "stale"
        else:
            status = "unavailable"
        jobs.append(
            {
                "key": key,
                "status": status,
                "last_status": row.last_status if row else None,
                "last_started_at": _iso(row.last_started_at) if row else None,
                "last_finished_at": _iso(finished_at),
                "next_run_at": _iso(next_run_at),
                "age_seconds": finished_age,
                "stale_after_seconds": maximum_age,
            }
        )

    statuses = [item["status"] for item in jobs]
    return {
        "status": _rollup(statuses),
        "expected": True,
        "enabled_job_count": len(enabled_keys),
        "jobs": jobs,
        "reason": None if statuses else "No scheduler jobs are enabled.",
    }


def production_freshness(schedule: dict, now: datetime | None = None) -> dict:
    """Build the public, sanitized production freshness report."""
    checked_at = _utc(now) or datetime.now(UTC)
    components = {
        "schedule": schedule_freshness(schedule),
        "providers": provider_freshness(checked_at),
        "scheduler": scheduler_freshness(checked_at),
    }
    blocking = [component["status"] for component in components.values() if component["status"] != "disabled"]
    overall = "ready" if blocking and all(status == "ready" for status in blocking) else "degraded"
    if blocking and all(status == "unavailable" for status in blocking):
        overall = "unavailable"
    return {
        "status": overall,
        "checked_at": checked_at.isoformat(),
        "components": components,
    }
