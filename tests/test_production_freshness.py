"""P1.4 production freshness reporting regression coverage."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import nfl_data
import production_freshness
import provider_health
import scheduled_jobs
from database import db
from db_models import DataSource, ScheduledJob


def _schedule(*, status: str = "ready") -> dict:
    return {
        "season": 2026,
        "ready": True,
        "freshness_status": status,
        "fetched_at": "2026-08-24T12:00:00+00:00",
        "age_seconds": 60.0,
        "stale_after_seconds": 21600,
        "total_games": 334,
        "current_week": {"season": 2026, "week": 3, "season_type": "PRE"},
        "issues": [],
    }


def test_schedule_status_reports_snapshot_age(monkeypatch):
    now = datetime(2026, 8, 24, 20, tzinfo=UTC)
    payload = nfl_data._read_json("schedule_2026.json")
    payload["fetched_at"] = (now - timedelta(hours=8)).timestamp()
    monkeypatch.setattr(nfl_data, "_read_json", lambda _name: payload)
    monkeypatch.setenv("SCHEDULE_FRESHNESS_MAX_AGE_SECONDS", "21600")

    status = nfl_data.schedule_status(2026, now=now)

    assert status["ready"] is True
    assert status["freshness_status"] == "stale"
    assert status["age_seconds"] == 28800.0
    assert status["stale_after_seconds"] == 21600
    assert status["fetched_at"] == "2026-08-24T12:00:00+00:00"


def test_provider_freshness_classifies_persistent_source(app_fixture):
    now = datetime(2026, 8, 24, 20, tzinfo=UTC)
    with app_fixture.app_context():
        source = DataSource(
            key="p1-4-test-source",
            name="P1.4 test source",
            source_type="http",
            enabled=True,
            refresh_interval_minutes=60,
            last_success_at=now - timedelta(minutes=30),
        )
        db.session.add(source)
        db.session.commit()
        try:
            report = production_freshness.provider_freshness(now)
            row = next(item for item in report["sources"] if item["key"] == source.key)
            assert row["status"] == "ready"
            assert row["age_seconds"] == 1800.0

            source.last_success_at = now - timedelta(hours=2)
            db.session.commit()
            report = production_freshness.provider_freshness(now)
            row = next(item for item in report["sources"] if item["key"] == source.key)
            assert row["status"] == "stale"

            source.last_failure_at = now - timedelta(minutes=5)
            db.session.commit()
            report = production_freshness.provider_freshness(now)
            row = next(item for item in report["sources"] if item["key"] == source.key)
            assert row["status"] == "degraded"
            assert "last_error" not in row
        finally:
            db.session.delete(source)
            db.session.commit()


def test_provider_http_telemetry_is_reported_without_error_details(app_fixture):
    with app_fixture.app_context():
        provider_health.reset()
        provider_health.record_failure("provider.example", "secret provider failure")
        report = production_freshness.provider_freshness(datetime.now(UTC))
        row = next(item for item in report["http_telemetry"] if item["key"] == "provider.example")
        assert row["status"] == "degraded"
        assert "last_error" not in row
        provider_health.reset()


def test_scheduler_freshness_is_explicitly_disabled(monkeypatch):
    monkeypatch.delenv("SCHEDULER_EXPECTED", raising=False)
    monkeypatch.delenv("ENABLE_SCHEDULER", raising=False)

    report = production_freshness.scheduler_freshness(datetime.now(UTC))

    assert report["status"] == "disabled"
    assert report["expected"] is False


def test_scheduler_freshness_uses_job_cadence(app_fixture, monkeypatch):
    now = datetime(2026, 8, 24, 20, tzinfo=UTC)
    monkeypatch.setenv("SCHEDULER_EXPECTED", "true")
    monkeypatch.setattr(production_freshness, "_job_enabled", lambda key: key == "cached-data-sync")
    monkeypatch.setattr(production_freshness, "_job_minutes", lambda _key: 60)
    with app_fixture.app_context():
        db.session.query(ScheduledJob).delete()
        row = ScheduledJob(
            key="cached-data-sync",
            name="Cached data sync",
            cron="every 60 minutes",
            enabled=True,
            last_status="scheduled",
            next_run_at=now + timedelta(minutes=30),
        )
        db.session.add(row)
        db.session.commit()

        report = production_freshness.scheduler_freshness(now)
        assert report["status"] == "ready"
        assert report["jobs"][0]["status"] == "pending"

        row.last_status = "success"
        row.last_finished_at = now - timedelta(minutes=30)
        db.session.commit()
        report = production_freshness.scheduler_freshness(now)
        assert report["jobs"][0]["status"] == "ready"

        row.last_finished_at = now - timedelta(hours=3)
        db.session.commit()
        report = production_freshness.scheduler_freshness(now)
        assert report["status"] == "stale"

        row.last_status = "failed"
        db.session.commit()
        report = production_freshness.scheduler_freshness(now)
        assert report["status"] == "degraded"
        db.session.query(ScheduledJob).delete()
        db.session.commit()


def test_scheduler_registration_persists_next_run(app_fixture, monkeypatch):
    next_run = datetime.now(UTC) + timedelta(minutes=60)

    class _Job:
        next_run_time = next_run

    class _Scheduler:
        def get_job(self, key):
            return _Job() if key == "cached-data-sync" else None

    monkeypatch.setattr(scheduled_jobs, "_scheduler", _Scheduler())
    monkeypatch.setattr(scheduled_jobs, "_job_enabled", lambda key: key == "cached-data-sync")
    with app_fixture.app_context():
        db.session.query(ScheduledJob).delete()
        scheduled_jobs._register_jobs(app_fixture)
        row = db.session.scalar(db.select(ScheduledJob).where(ScheduledJob.key == "cached-data-sync"))
        assert row.enabled is True
        assert row.last_status == "scheduled"
        assert row.next_run_at is not None
        db.session.query(ScheduledJob).delete()
        db.session.commit()


def test_health_and_ready_expose_all_freshness_components(client, monkeypatch):
    monkeypatch.setattr(nfl_data, "schedule_status", lambda: _schedule(status="stale"))
    monkeypatch.setattr(
        production_freshness,
        "provider_freshness",
        lambda _now: {"status": "ready", "sources": [], "http_telemetry": []},
    )
    monkeypatch.setattr(
        production_freshness,
        "scheduler_freshness",
        lambda _now: {"status": "ready", "expected": True, "jobs": []},
    )

    health = client.get("/health")
    ready = client.get("/ready")

    assert health.status_code == 200
    assert ready.status_code == 200
    assert health.get_json()["status"] == "degraded"
    assert ready.get_json()["ok"] is True
    assert ready.get_json()["status"] == "degraded"
    assert set(ready.get_json()["freshness"]["components"]) == {
        "schedule",
        "providers",
        "scheduler",
    }


def test_deployment_gate_requires_freshness_contract():
    workflow = (Path(__file__).resolve().parents[1] / ".github" / "workflows" / "fly.yml").read_text(
        encoding="utf-8"
    )

    assert '{"schedule", "providers", "scheduler"}.issubset(components)' in workflow
