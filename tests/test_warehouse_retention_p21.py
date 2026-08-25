"""P2.1 warehouse retention safety contracts."""

from datetime import UTC, datetime, timedelta

from database import db
from db_models import DataQualityIssue, DataSource, DataSyncRun, RawIngestRecord
from warehouse_retention import WarehouseRetentionPolicy, apply_warehouse_retention


def _policy() -> WarehouseRetentionPolicy:
    return WarehouseRetentionPolicy(
        raw_retention_days=30,
        raw_versions_per_entity=1,
        sync_run_retention_days=30,
        sync_runs_per_source=1,
        resolved_quality_retention_days=7,
        batch_size=100,
    )


def test_retention_is_preview_first_and_preserves_latest(app_fixture):
    now = datetime(2026, 8, 24, tzinfo=UTC)
    with app_fixture.app_context():
        source = DataSource(key="p21-retention-source", name="P2.1 retention source")
        db.session.add(source)
        db.session.flush()
        for index, age in enumerate((300, 200, 5), start=1):
            db.session.add(
                RawIngestRecord(
                    source_id=source.id,
                    entity_type="roster",
                    external_id="same-player",
                    payload={"version": index},
                    payload_hash=f"{index:064d}",
                    observed_at=now - timedelta(days=age),
                    ingested_at=now - timedelta(days=age),
                )
            )
        db.session.flush()

        preview = apply_warehouse_retention(dry_run=True, now=now, policy=_policy())
        assert preview["candidates"]["raw_ingest_records"] == 2
        assert preview["deleted"]["raw_ingest_records"] == 0
        assert (
            db.session.scalar(
                db.select(db.func.count())
                .select_from(RawIngestRecord)
                .where(RawIngestRecord.source_id == source.id)
            )
            == 3
        )

        applied = apply_warehouse_retention(dry_run=False, now=now, policy=_policy())
        assert applied["deleted"]["raw_ingest_records"] == 2
        remaining = db.session.scalars(
            db.select(RawIngestRecord).where(RawIngestRecord.source_id == source.id)
        ).all()
        assert len(remaining) == 1
        assert remaining[0].payload == {"version": 3}

        db.session.delete(source)
        db.session.commit()


def test_retention_keeps_recent_sync_history_and_unresolved_issues(app_fixture):
    now = datetime(2026, 8, 24, tzinfo=UTC)
    with app_fixture.app_context():
        for index, age in enumerate((300, 200, 100, 1), start=1):
            db.session.add(
                DataSyncRun(
                    source="p21-retention-sync",
                    started_at=now - timedelta(days=age, minutes=1),
                    finished_at=now - timedelta(days=age),
                    status="success",
                    records_read=index,
                    records_written=index,
                )
            )
        db.session.add_all(
            [
                DataQualityIssue(
                    check_name="p21_resolved",
                    severity="warning",
                    entity_type="player",
                    entity_id="p21-old-resolved",
                    message="old resolved issue",
                    resolved=True,
                    detected_at=now - timedelta(days=100),
                    resolved_at=now - timedelta(days=90),
                ),
                DataQualityIssue(
                    check_name="p21_unresolved",
                    severity="warning",
                    entity_type="player",
                    entity_id="p21-old-unresolved",
                    message="old unresolved issue",
                    resolved=False,
                    detected_at=now - timedelta(days=100),
                ),
            ]
        )
        db.session.flush()

        applied = apply_warehouse_retention(dry_run=False, now=now, policy=_policy())
        assert applied["deleted"]["data_sync_runs"] == 3
        assert applied["deleted"]["resolved_quality_issues"] == 1
        assert (
            db.session.scalar(
                db.select(db.func.count())
                .select_from(DataSyncRun)
                .where(DataSyncRun.source == "p21-retention-sync")
            )
            == 1
        )
        assert (
            db.session.scalar(
                db.select(DataQualityIssue).where(DataQualityIssue.entity_id == "p21-old-unresolved")
            )
            is not None
        )

        db.session.execute(db.delete(DataSyncRun).where(DataSyncRun.source == "p21-retention-sync"))
        db.session.execute(
            db.delete(DataQualityIssue).where(
                DataQualityIssue.entity_id.in_(("p21-old-resolved", "p21-old-unresolved"))
            )
        )
        db.session.commit()


def test_retention_admin_requires_explicit_confirmation(client):
    preview = client.get("/api/admin/warehouse-retention")
    rejected = client.post("/api/admin/warehouse-retention/apply", json={})

    assert preview.status_code == 200
    assert preview.get_json()["dry_run"] is True
    assert rejected.status_code == 400
    assert rejected.get_json()["error"] == "confirmation_required"


def test_retention_scheduler_is_fail_closed(app_fixture, monkeypatch):
    import scheduled_jobs
    import warehouse_retention

    calls = []
    records = []
    monkeypatch.delenv("ENABLE_WAREHOUSE_RETENTION", raising=False)
    monkeypatch.setattr(
        warehouse_retention,
        "apply_warehouse_retention",
        lambda **kwargs: calls.append(kwargs),
    )
    monkeypatch.setattr(
        scheduled_jobs,
        "_record",
        lambda key, status, error=None: records.append((key, status, error)),
    )

    scheduled_jobs.run_job(app_fixture, "warehouse-retention")
    assert calls == []
    assert records[0][1] == "skipped"

    monkeypatch.setenv("ENABLE_WAREHOUSE_RETENTION", "true")
    scheduled_jobs.run_job(app_fixture, "warehouse-retention")
    assert calls == [{"dry_run": False}]


def test_retention_does_not_overlap_provider_sync(app_fixture, monkeypatch):
    import scheduled_jobs

    records = []
    monkeypatch.setenv("ENABLE_WAREHOUSE_RETENTION", "true")
    monkeypatch.setattr(
        scheduled_jobs,
        "_record",
        lambda key, status, error=None: records.append((key, status, error)),
    )

    scheduled_jobs._PROVIDER_SYNC_LOCK.acquire()
    try:
        scheduled_jobs.run_job(app_fixture, "warehouse-retention")
    finally:
        scheduled_jobs._PROVIDER_SYNC_LOCK.release()

    assert records == [
        (
            "warehouse-retention",
            "skipped",
            "another provider sync or retention run is already running",
        )
    ]
