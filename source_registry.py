"""Data source registry and raw provenance capture."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from sqlalchemy import select

from database import db
from db_models import DataSource, RawIngestRecord


def register_source(key: str, name: str, *, source_type: str = "file", base_url: str | None = None,
                    license_name: str | None = None, attribution: str | None = None,
                    refresh_interval_minutes: int | None = None, metadata: dict | None = None) -> DataSource:
    source = db.session.scalar(select(DataSource).where(DataSource.key == key))
    if not source:
        source = DataSource(key=key, name=name)
        db.session.add(source)
    source.name = name
    source.source_type = source_type
    source.base_url = base_url
    source.license_name = license_name
    source.attribution = attribution
    source.refresh_interval_minutes = refresh_interval_minutes
    source.metadata_json = metadata or source.metadata_json
    db.session.flush()
    return source


def capture_raw(source: DataSource, entity_type: str, external_id: str, payload: dict,
                *, season: int | None = None, week: int | None = None,
                observed_at: datetime | None = None) -> bool:
    # Providers hand us dates, Decimals and numpy scalars. The hash has always
    # coerced them via default=str; the stored payload must be coerced the same
    # way or the column's own json.dumps raises and kills the whole sync.
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    payload = json.loads(canonical)
    payload_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    existing = db.session.scalar(select(RawIngestRecord.id).where(
        RawIngestRecord.source_id == source.id,
        RawIngestRecord.entity_type == entity_type,
        RawIngestRecord.external_id == str(external_id),
        RawIngestRecord.payload_hash == payload_hash,
    ))
    if existing:
        return False
    db.session.add(RawIngestRecord(
        source_id=source.id,
        entity_type=entity_type,
        external_id=str(external_id),
        season=season,
        week=week,
        payload=payload,
        payload_hash=payload_hash,
        observed_at=observed_at or datetime.now(timezone.utc),
    ))
    return True
