"""Durable provider-cache storage backed by the application database.

Fly Machines have ephemeral root filesystems. Provider snapshots therefore
cannot rely on files under ``/app/data`` if they need to survive a deploy,
restart, or machine replacement. This module stores the canonical cache in the
same PostgreSQL database as the warehouse while keeping the existing JSON file
as an optional local-development mirror.

The store intentionally uses a tiny provider/cache-key table rather than a
provider-specific schema. One row stores the shared game-event catalog and one
row per event stores its player-prop snapshot. Writes are upserts and never
remove rows that are absent from an in-memory snapshot, so a partially loaded
machine cannot accidentally erase durable provider history.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.exc import SQLAlchemyError

from database import db


provider_cache_snapshots = sa.Table(
    "provider_cache_snapshots",
    db.metadata,
    sa.Column("provider_key", sa.String(80), primary_key=True),
    sa.Column("cache_key", sa.String(180), primary_key=True),
    sa.Column("payload", sa.JSON, nullable=False),
    sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    ),
    sa.Column(
        "updated_at",
        sa.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    ),
)

_GAME_KEY = "game_odds"
_EVENT_PREFIX = "event_props:"


def flatten_snapshot(snapshot: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    """Convert the legacy nested snapshot into durable row payloads."""
    if not isinstance(snapshot, dict):
        return {}
    records: dict[str, dict[str, Any]] = {}
    game = snapshot.get("game_odds")
    if isinstance(game, dict):
        records[_GAME_KEY] = dict(game)
    event_props = snapshot.get("event_props")
    if isinstance(event_props, dict):
        for event_id, payload in event_props.items():
            if isinstance(payload, dict) and str(event_id):
                records[f"{_EVENT_PREFIX}{event_id}"] = dict(payload)
    return records


def inflate_records(records: dict[str, Any] | None) -> dict[str, Any]:
    """Rebuild the legacy ``odds_api`` snapshot shape from durable rows."""
    snapshot: dict[str, Any] = {}
    event_props: dict[str, Any] = {}
    for cache_key, payload in (records or {}).items():
        if not isinstance(payload, dict):
            continue
        key = str(cache_key)
        if key == _GAME_KEY:
            snapshot["game_odds"] = dict(payload)
        elif key.startswith(_EVENT_PREFIX):
            event_id = key[len(_EVENT_PREFIX) :]
            if event_id:
                event_props[event_id] = dict(payload)
    if event_props:
        snapshot["event_props"] = event_props
    return snapshot


def _rollback_quietly() -> None:
    try:
        db.session.rollback()
    except Exception:  # noqa: BLE001 - best-effort cleanup outside app context
        pass


def load_snapshot(provider_key: str) -> dict[str, Any]:
    """Load all durable cache rows for one provider.

    Returns an empty mapping when called outside a Flask application context or
    when the database is temporarily unavailable, allowing local file fallback.
    """
    try:
        rows = db.session.execute(
            sa.select(
                provider_cache_snapshots.c.cache_key,
                provider_cache_snapshots.c.payload,
            ).where(provider_cache_snapshots.c.provider_key == provider_key)
        ).all()
    except (RuntimeError, SQLAlchemyError):
        _rollback_quietly()
        return {}
    return inflate_records({str(row.cache_key): row.payload for row in rows})


def save_snapshot(provider_key: str, snapshot: dict[str, Any]) -> bool:
    """Upsert durable rows represented by ``snapshot``.

    Existing rows not present in this snapshot are intentionally retained. This
    prevents a new/partially hydrated machine from deleting cache data written
    by another machine.
    """
    records = flatten_snapshot(snapshot)
    if not records:
        return False
    now = datetime.now(UTC)
    try:
        for cache_key, payload in records.items():
            exists = db.session.execute(
                sa.select(provider_cache_snapshots.c.cache_key).where(
                    provider_cache_snapshots.c.provider_key == provider_key,
                    provider_cache_snapshots.c.cache_key == cache_key,
                )
            ).scalar_one_or_none()
            if exists is None:
                db.session.execute(
                    provider_cache_snapshots.insert().values(
                        provider_key=provider_key,
                        cache_key=cache_key,
                        payload=payload,
                        created_at=now,
                        updated_at=now,
                    )
                )
            else:
                db.session.execute(
                    provider_cache_snapshots.update()
                    .where(
                        provider_cache_snapshots.c.provider_key == provider_key,
                        provider_cache_snapshots.c.cache_key == cache_key,
                    )
                    .values(payload=payload, updated_at=now)
                )
        db.session.commit()
        return True
    except (RuntimeError, SQLAlchemyError):
        _rollback_quietly()
        return False


def cache_status(provider_key: str) -> dict[str, Any]:
    """Return sanitized persistence metadata for readiness/verification output."""
    try:
        rows = db.session.execute(
            sa.select(
                provider_cache_snapshots.c.cache_key,
                provider_cache_snapshots.c.updated_at,
            ).where(provider_cache_snapshots.c.provider_key == provider_key)
        ).all()
    except (RuntimeError, SQLAlchemyError):
        _rollback_quietly()
        return {
            "backend": "database",
            "available": False,
            "rows": 0,
            "gameCatalogRows": 0,
            "eventPropRows": 0,
            "latestUpdatedAt": None,
        }
    latest = max((row.updated_at for row in rows if row.updated_at is not None), default=None)
    keys = [str(row.cache_key) for row in rows]
    return {
        "backend": "database",
        "available": True,
        "rows": len(rows),
        "gameCatalogRows": sum(1 for key in keys if key == _GAME_KEY),
        "eventPropRows": sum(1 for key in keys if key.startswith(_EVENT_PREFIX)),
        "latestUpdatedAt": latest.isoformat() if latest is not None else None,
    }
