from __future__ import annotations

from datetime import UTC, datetime, timedelta

from scripts import p2_exit_verification as p2


def _sync(
    status: str,
    *,
    started_seconds_ago: float | None = None,
    finished_seconds_ago: float | None = None,
    error_category=None,
    error_fingerprint=None,
) -> dict:
    now = datetime(2026, 8, 29, 0, 10, tzinfo=UTC)
    return {
        "status": status,
        "started_at": (
            (now - timedelta(seconds=started_seconds_ago)).isoformat()
            if started_seconds_ago is not None
            else None
        ),
        "finished_at": (
            (now - timedelta(seconds=finished_seconds_ago)).isoformat()
            if finished_seconds_ago is not None
            else None
        ),
        "records_read": 100,
        "records_written": 100,
        "error_category": error_category,
        "error_fingerprint": error_fingerprint,
    }


def test_completed_latest_sync_passes():
    now = datetime(2026, 8, 29, 0, 10, tzinfo=UTC)
    latest = _sync("completed", started_seconds_ago=120, finished_seconds_ago=30)
    ok, details = p2._cache_sync_health(latest, latest, now=now)
    assert ok is True
    assert details["mode"] == "latest-completed"


def test_active_sync_with_recent_completed_fallback_passes():
    now = datetime(2026, 8, 29, 0, 10, tzinfo=UTC)
    active = _sync("running", started_seconds_ago=24 * 60)
    completed = _sync("completed", started_seconds_ago=10 * 3600 + 60, finished_seconds_ago=10 * 3600)
    ok, details = p2._cache_sync_health(active, completed, now=now)
    assert ok is True
    assert details["mode"] == "active-with-recent-completed-fallback"
    assert details["active_run_age_seconds"] == 1440.0


def test_stale_running_sync_still_blocks():
    now = datetime(2026, 8, 29, 0, 10, tzinfo=UTC)
    active = _sync("running", started_seconds_ago=p2.CACHE_SYNC_RUNNING_MAX_SECONDS + 1)
    completed = _sync("completed", finished_seconds_ago=3600)
    ok, details = p2._cache_sync_health(active, completed, now=now)
    assert ok is False
    assert details["mode"] == "active-without-safe-fallback"


def test_active_sync_without_recent_completed_fallback_blocks():
    now = datetime(2026, 8, 29, 0, 10, tzinfo=UTC)
    active = _sync("running", started_seconds_ago=600)
    completed = _sync(
        "completed",
        finished_seconds_ago=p2.CACHE_SYNC_COMPLETED_MAX_SECONDS + 1,
    )
    ok, _ = p2._cache_sync_health(active, completed, now=now)
    assert ok is False


def test_failed_latest_sync_blocks_even_with_prior_success():
    now = datetime(2026, 8, 29, 0, 10, tzinfo=UTC)
    failed = _sync(
        "failed",
        started_seconds_ago=120,
        finished_seconds_ago=30,
        error_category="database_operational",
        error_fingerprint="abc123",
    )
    completed = _sync("completed", finished_seconds_ago=3600)
    ok, details = p2._cache_sync_health(failed, completed, now=now)
    assert ok is False
    assert details["mode"] == "unhealthy-latest-sync"
