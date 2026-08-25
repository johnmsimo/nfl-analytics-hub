"""Bounded, auditable retention for high-churn warehouse operational data."""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select

from database import db
from db_models import DataQualityIssue, DataSyncRun, RawIngestRecord


def _env_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    raw = os.environ.get(name)
    value = default if raw in (None, "") else int(raw)
    if value < minimum or value > maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


@dataclass(frozen=True)
class WarehouseRetentionPolicy:
    raw_retention_days: int
    raw_versions_per_entity: int
    sync_run_retention_days: int
    sync_runs_per_source: int
    resolved_quality_retention_days: int
    batch_size: int

    def as_dict(self) -> dict[str, int]:
        return asdict(self)


def retention_policy() -> WarehouseRetentionPolicy:
    return WarehouseRetentionPolicy(
        raw_retention_days=_env_int("WAREHOUSE_RAW_RETENTION_DAYS", 180, minimum=30, maximum=3650),
        raw_versions_per_entity=_env_int("WAREHOUSE_RAW_VERSIONS_PER_ENTITY", 5, minimum=1, maximum=100),
        sync_run_retention_days=_env_int("WAREHOUSE_SYNC_RUN_RETENTION_DAYS", 180, minimum=30, maximum=3650),
        sync_runs_per_source=_env_int("WAREHOUSE_SYNC_RUNS_PER_SOURCE", 25, minimum=1, maximum=1000),
        resolved_quality_retention_days=_env_int(
            "WAREHOUSE_RESOLVED_QUALITY_RETENTION_DAYS", 90, minimum=7, maximum=3650
        ),
        batch_size=_env_int("WAREHOUSE_RETENTION_BATCH_SIZE", 5000, minimum=100, maximum=50000),
    )


def _ranked_candidate_statement(
    model,
    *,
    timestamp_column,
    partition_by: tuple,
    keep: int,
    cutoff: datetime,
    filters: tuple = (),
):
    ranked = (
        select(
            model.id.label("record_id"),
            timestamp_column.label("retained_at"),
            func.row_number()
            .over(
                partition_by=partition_by,
                order_by=(timestamp_column.desc(), model.id.desc()),
            )
            .label("version_rank"),
        )
        .where(*filters)
        .subquery()
    )
    return select(ranked.c.record_id).where(
        ranked.c.version_rank > keep,
        ranked.c.retained_at < cutoff,
    )


def _candidate_count(statement) -> int:
    return int(db.session.scalar(select(func.count()).select_from(statement.subquery())) or 0)


def _delete_candidates(model, statement, batch_size: int) -> int:
    deleted = 0
    while True:
        ids = list(db.session.scalars(statement.limit(batch_size)).all())
        if not ids:
            break
        db.session.execute(delete(model).where(model.id.in_(ids)))
        db.session.flush()
        deleted += len(ids)
    return deleted


def retention_preview(
    *,
    now: datetime | None = None,
    policy: WarehouseRetentionPolicy | None = None,
) -> dict:
    current = now or datetime.now(UTC)
    active_policy = policy or retention_policy()
    raw_statement = _ranked_candidate_statement(
        RawIngestRecord,
        timestamp_column=RawIngestRecord.ingested_at,
        partition_by=(
            RawIngestRecord.source_id,
            RawIngestRecord.entity_type,
            RawIngestRecord.external_id,
        ),
        keep=active_policy.raw_versions_per_entity,
        cutoff=current - timedelta(days=active_policy.raw_retention_days),
    )
    sync_statement = _ranked_candidate_statement(
        DataSyncRun,
        timestamp_column=DataSyncRun.finished_at,
        partition_by=(DataSyncRun.source,),
        keep=active_policy.sync_runs_per_source,
        cutoff=current - timedelta(days=active_policy.sync_run_retention_days),
        filters=(DataSyncRun.finished_at.is_not(None), DataSyncRun.status != "running"),
    )
    quality_cutoff = current - timedelta(days=active_policy.resolved_quality_retention_days)
    quality_statement = select(DataQualityIssue.id).where(
        DataQualityIssue.resolved.is_(True),
        func.coalesce(DataQualityIssue.resolved_at, DataQualityIssue.detected_at) < quality_cutoff,
    )
    return {
        "generated_at": current.isoformat(),
        "policy": active_policy.as_dict(),
        "candidates": {
            "raw_ingest_records": _candidate_count(raw_statement),
            "data_sync_runs": _candidate_count(sync_statement),
            "resolved_quality_issues": _candidate_count(quality_statement),
        },
        "_statements": (raw_statement, sync_statement, quality_statement),
    }


def apply_warehouse_retention(
    *,
    dry_run: bool = True,
    now: datetime | None = None,
    policy: WarehouseRetentionPolicy | None = None,
) -> dict:
    preview = retention_preview(now=now, policy=policy)
    statements = preview.pop("_statements")
    result = {
        **preview,
        "dry_run": dry_run,
        "deleted": {
            "raw_ingest_records": 0,
            "data_sync_runs": 0,
            "resolved_quality_issues": 0,
        },
    }
    if dry_run:
        return result

    active_policy = policy or retention_policy()
    try:
        for key, model, statement in (
            ("raw_ingest_records", RawIngestRecord, statements[0]),
            ("data_sync_runs", DataSyncRun, statements[1]),
            ("resolved_quality_issues", DataQualityIssue, statements[2]),
        ):
            result["deleted"][key] = _delete_candidates(
                model,
                statement,
                active_policy.batch_size,
            )
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    return result
