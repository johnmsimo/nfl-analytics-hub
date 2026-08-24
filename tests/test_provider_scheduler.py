from __future__ import annotations

import pytest

import scheduled_jobs


EXTERNAL_JOB_KEYS = {
    "external-rosters-sync",
    "external-injuries-sync",
    "external-depth-charts-sync",
    "external-pbp-sync",
    "external-snap-counts-sync",
    "external-player-stats-sync",
}
COMMERCIAL_JOB_KEYS = {
    "commercial-weather-sync",
    "commercial-odds-sync",
    "commercial-live-games-sync",
    "commercial-coaches-sync",
    "commercial-transactions-sync",
}


def _enable(monkeypatch, family: str, dataset: str) -> None:
    monkeypatch.setenv(f"ENABLE_{family.upper()}_SYNC", "true")
    monkeypatch.setenv(
        f"ENABLE_{family.upper()}_{dataset.upper()}_SYNC",
        "true",
    )


def test_provider_jobs_are_dataset_specific():
    assert "external-data-sync" not in scheduled_jobs.JOBS
    assert "commercial-data-sync" not in scheduled_jobs.JOBS
    assert EXTERNAL_JOB_KEYS <= scheduled_jobs.JOBS.keys()
    assert COMMERCIAL_JOB_KEYS <= scheduled_jobs.JOBS.keys()


def test_family_gate_alone_does_not_enable_every_dataset(monkeypatch):
    monkeypatch.setenv("ENABLE_EXTERNAL_SYNC", "true")
    monkeypatch.delenv("ENABLE_EXTERNAL_ROSTERS_SYNC", raising=False)
    monkeypatch.delenv("ENABLE_EXTERNAL_INJURIES_SYNC", raising=False)

    assert scheduled_jobs._job_enabled("external-rosters-sync") is False
    assert scheduled_jobs._job_enabled("external-injuries-sync") is False

    monkeypatch.setenv("ENABLE_EXTERNAL_ROSTERS_SYNC", "yes")

    assert scheduled_jobs._job_enabled("external-rosters-sync") is True
    assert scheduled_jobs._job_enabled("external-injuries-sync") is False


def test_dataset_cadence_can_be_overridden_safely(monkeypatch):
    assert scheduled_jobs._job_minutes("external-depth-charts-sync") == 1440

    monkeypatch.setenv("EXTERNAL_DEPTH_CHARTS_INTERVAL_MINUTES", "720")
    assert scheduled_jobs._job_minutes("external-depth-charts-sync") == 720

    monkeypatch.setenv("EXTERNAL_DEPTH_CHARTS_INTERVAL_MINUTES", "0")
    with pytest.raises(ValueError, match="at least 1 minute"):
        scheduled_jobs._job_minutes("external-depth-charts-sync")


def test_external_job_syncs_only_its_dataset(app_fixture, monkeypatch):
    calls = []
    _enable(monkeypatch, "external", "rosters")
    monkeypatch.setenv("EXTERNAL_DATA_SEASON", "2026")
    monkeypatch.setattr(scheduled_jobs, "_record", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        scheduled_jobs,
        "sync_external",
        lambda season, datasets: calls.append((season, datasets)),
    )

    scheduled_jobs.run_job(app_fixture, "external-rosters-sync")

    assert calls == [(2026, ["rosters"])]


def test_weekly_commercial_job_defaults_to_current_nfl_week(app_fixture, monkeypatch):
    calls = []
    _enable(monkeypatch, "commercial", "weather")
    monkeypatch.setenv("EXTERNAL_DATA_SEASON", "2026")
    monkeypatch.delenv("EXTERNAL_DATA_WEEK", raising=False)
    monkeypatch.setattr(scheduled_jobs, "_record", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        scheduled_jobs.nfl_data,
        "current_week",
        lambda season: {"season": season, "week": 3, "season_type": "PRE"},
    )
    monkeypatch.setattr(
        scheduled_jobs,
        "sync_commercial",
        lambda season, datasets, week: calls.append((season, datasets, week)),
    )

    scheduled_jobs.run_job(app_fixture, "commercial-weather-sync")

    assert calls == [(2026, ["weather"], 3)]


def test_provider_jobs_do_not_overlap(app_fixture, monkeypatch):
    calls = []
    records = []
    _enable(monkeypatch, "external", "injuries")
    monkeypatch.setattr(
        scheduled_jobs,
        "sync_external",
        lambda *_args, **_kwargs: calls.append("sync"),
    )
    monkeypatch.setattr(
        scheduled_jobs,
        "_record",
        lambda key, status, error=None: records.append((key, status, error)),
    )

    scheduled_jobs._PROVIDER_SYNC_LOCK.acquire()
    try:
        scheduled_jobs.run_job(app_fixture, "external-injuries-sync")
    finally:
        scheduled_jobs._PROVIDER_SYNC_LOCK.release()

    assert calls == []
    assert records == [
        (
            "external-injuries-sync",
            "skipped",
            "another provider sync is already running",
        )
    ]


def test_manual_provider_routes_require_explicit_datasets(client):
    external = client.post("/api/admin/external-sync?season=2026")
    commercial = client.post("/api/admin/commercial-sync?season=2026")

    assert external.status_code == 400
    assert external.get_json()["error"] == "datasets_required"
    assert commercial.status_code == 400
    assert commercial.get_json()["error"] == "datasets_required"
