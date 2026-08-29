#!/usr/bin/env python3
"""Print a sanitized, strictly read-only P2.1 production preview."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select

from database import db
from db_models import (
    DataQualityIssue,
    DataSyncRun,
    Player,
    PlayerExternalIdentity,
    RawIngestRecord,
    ScheduledJob,
)
from player_identity import reconcile_raw_player_identities
from warehouse_retention import apply_warehouse_retention


def _error_category(error: str | None) -> str | None:
    if not error:
        return None
    folded = error.lower()
    if "no such table" in folded or "undefined table" in folded:
        return "schema_missing_table"
    if any(term in folded for term in ("no such column", "undefined column", "undefinedcolumn")):
        return "schema_missing_column"
    if any(term in folded for term in ("unique constraint", "duplicate key", "integrityerror")):
        return "database_integrity"
    if any(term in folded for term in ("operationalerror", "connection refused", "connection reset")):
        return "database_operational"
    if any(term in folded for term in ("jsondecodeerror", "csv", "filenotfounderror")):
        return "cache_input"
    return "unclassified"


def _iso(value) -> str | None:
    return value.isoformat() if value else None


def _sync_payload(run: DataSyncRun | None) -> dict[str, Any] | None:
    if not run:
        return None
    error = str(run.error or "")
    return {
        "id": run.id,
        "status": run.status,
        "started_at": _iso(run.started_at),
        "finished_at": _iso(run.finished_at),
        "records_read": run.records_read,
        "records_written": run.records_written,
        "error_category": _error_category(error),
        "error_fingerprint": (hashlib.sha256(error.encode("utf-8")).hexdigest()[:12] if error else None),
    }


def _latest_cache_sync() -> dict[str, Any] | None:
    run = db.session.scalar(
        select(DataSyncRun)
        .where(DataSyncRun.source == "local-cache")
        .order_by(DataSyncRun.id.desc())
        .limit(1)
    )
    return _sync_payload(run)


def _last_completed_cache_sync() -> dict[str, Any] | None:
    run = db.session.scalar(
        select(DataSyncRun)
        .where(
            DataSyncRun.source == "local-cache",
            DataSyncRun.status == "completed",
        )
        .order_by(DataSyncRun.id.desc())
        .limit(1)
    )
    return _sync_payload(run)


def _warehouse_counts() -> dict[str, int]:
    return {
        "players": int(db.session.scalar(select(func.count()).select_from(Player)) or 0),
        "player_identities": int(
            db.session.scalar(select(func.count()).select_from(PlayerExternalIdentity)) or 0
        ),
        "raw_ingest_records": int(db.session.scalar(select(func.count()).select_from(RawIngestRecord)) or 0),
        "sync_runs": int(db.session.scalar(select(func.count()).select_from(DataSyncRun)) or 0),
        "quality_issues": int(db.session.scalar(select(func.count()).select_from(DataQualityIssue)) or 0),
    }


def build_preview() -> dict[str, Any]:
    """Return sanitized previews and prove the preview path made no row changes."""
    before = _warehouse_counts()
    identity = reconcile_raw_player_identities(dry_run=True)
    retention = apply_warehouse_retention(dry_run=True)
    after = _warehouse_counts()

    if before != after:
        raise RuntimeError("read_only_invariant_failed: warehouse counts changed")
    if identity.get("dry_run") is not True or retention.get("dry_run") is not True:
        raise RuntimeError("read_only_invariant_failed: preview did not remain dry-run")
    if identity.get("players_merged") != 0 or identity.get("identity_links_added") != 0:
        raise RuntimeError("read_only_invariant_failed: identity preview reported mutations")
    if any(int(value or 0) for value in retention.get("deleted", {}).values()):
        raise RuntimeError("read_only_invariant_failed: retention preview reported deletions")

    retention_job = db.session.scalar(select(ScheduledJob).where(ScheduledJob.key == "warehouse-retention"))
    return {
        "ok": True,
        "generated_at": datetime.now(UTC).isoformat(),
        "mode": "read-only",
        "warehouse_counts": before,
        "identity_reconciliation": identity,
        "warehouse_retention": retention,
        "warehouse_retention_scheduler": {
            "enabled": bool(retention_job and retention_job.enabled),
            "last_status": retention_job.last_status if retention_job else None,
        },
        "latest_cached_data_sync": _latest_cache_sync(),
        "last_completed_cached_data_sync": _last_completed_cache_sync(),
    }


def main() -> int:
    from app import app

    with app.app_context():
        result = build_preview()
        db.session.rollback()
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
