"""Durable PostgreSQL persistence for the legacy Tracker state.

The Tracker historically stored both picks and bankroll settings in JSON files
under ``data/``. Fly Machines use ephemeral root filesystems, so those files can
vanish on deploy/restart/machine replacement. P3.7 keeps the existing public
Tracker contract while making PostgreSQL the canonical store.

The legacy files remain best-effort mirrors/fallbacks for local development.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.exc import SQLAlchemyError

from database import db


tracker_day_snapshots = sa.Table(
    "tracker_day_snapshots",
    db.metadata,
    sa.Column("event_date", sa.String(10), primary_key=True),
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

tracker_settings_snapshots = sa.Table(
    "tracker_settings_snapshots",
    db.metadata,
    sa.Column("settings_key", sa.String(40), primary_key=True),
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

_SETTINGS_KEY = "default"


def _rollback_quietly() -> None:
    try:
        db.session.rollback()
    except Exception:  # noqa: BLE001 - best effort outside app context
        pass


def load_store() -> dict[str, Any]:
    """Return the exact legacy ``{date: day_payload}`` shape from PostgreSQL."""
    try:
        rows = db.session.execute(
            sa.select(tracker_day_snapshots.c.event_date, tracker_day_snapshots.c.payload)
        ).all()
    except (RuntimeError, SQLAlchemyError):
        _rollback_quietly()
        return {}
    return {
        str(row.event_date): dict(row.payload)
        for row in rows
        if isinstance(row.payload, dict)
    }


def save_store(store: dict[str, Any]) -> bool:
    """Synchronize the canonical day snapshots to PostgreSQL in one transaction."""
    if not isinstance(store, dict):
        return False
    now = datetime.now(UTC)
    try:
        wanted_dates = {str(date) for date in store}
        existing_dates = set(
            db.session.execute(sa.select(tracker_day_snapshots.c.event_date)).scalars().all()
        )
        for date, payload in store.items():
            if not isinstance(payload, dict):
                continue
            event_date = str(date)
            exists = event_date in existing_dates
            if exists:
                db.session.execute(
                    tracker_day_snapshots.update()
                    .where(tracker_day_snapshots.c.event_date == event_date)
                    .values(payload=payload, updated_at=now)
                )
            else:
                db.session.execute(
                    tracker_day_snapshots.insert().values(
                        event_date=event_date,
                        payload=payload,
                        created_at=now,
                        updated_at=now,
                    )
                )
        stale_dates = existing_dates - wanted_dates
        if stale_dates:
            db.session.execute(
                tracker_day_snapshots.delete().where(
                    tracker_day_snapshots.c.event_date.in_(sorted(stale_dates))
                )
            )
        db.session.commit()
        return True
    except (RuntimeError, SQLAlchemyError):
        _rollback_quietly()
        return False


def load_settings() -> dict[str, Any]:
    try:
        payload = db.session.execute(
            sa.select(tracker_settings_snapshots.c.payload).where(
                tracker_settings_snapshots.c.settings_key == _SETTINGS_KEY
            )
        ).scalar_one_or_none()
    except (RuntimeError, SQLAlchemyError):
        _rollback_quietly()
        return {}
    return dict(payload) if isinstance(payload, dict) else {}


def save_settings(payload: dict[str, Any]) -> bool:
    if not isinstance(payload, dict):
        return False
    now = datetime.now(UTC)
    try:
        exists = db.session.execute(
            sa.select(tracker_settings_snapshots.c.settings_key).where(
                tracker_settings_snapshots.c.settings_key == _SETTINGS_KEY
            )
        ).scalar_one_or_none()
        if exists is None:
            db.session.execute(
                tracker_settings_snapshots.insert().values(
                    settings_key=_SETTINGS_KEY,
                    payload=payload,
                    created_at=now,
                    updated_at=now,
                )
            )
        else:
            db.session.execute(
                tracker_settings_snapshots.update()
                .where(tracker_settings_snapshots.c.settings_key == _SETTINGS_KEY)
                .values(payload=payload, updated_at=now)
            )
        db.session.commit()
        return True
    except (RuntimeError, SQLAlchemyError):
        _rollback_quietly()
        return False


def persistence_status() -> dict[str, Any]:
    """Sanitized status for product/production verification."""
    try:
        day_rows = db.session.execute(
            sa.select(
                tracker_day_snapshots.c.event_date,
                tracker_day_snapshots.c.payload,
                tracker_day_snapshots.c.updated_at,
            )
        ).all()
        settings_row = db.session.execute(
            sa.select(
                tracker_settings_snapshots.c.updated_at,
            ).where(tracker_settings_snapshots.c.settings_key == _SETTINGS_KEY)
        ).first()
    except (RuntimeError, SQLAlchemyError):
        _rollback_quietly()
        return {
            "backend": "database",
            "available": False,
            "days": 0,
            "entries": 0,
            "settingsPersisted": False,
            "latestUpdatedAt": None,
        }
    entries = 0
    timestamps = []
    for row in day_rows:
        payload = row.payload if isinstance(row.payload, dict) else {}
        entries += len(payload.get("entries") or [])
        if row.updated_at is not None:
            timestamps.append(row.updated_at)
    if settings_row is not None and settings_row.updated_at is not None:
        timestamps.append(settings_row.updated_at)
    latest = max(timestamps, default=None)
    return {
        "backend": "database",
        "available": True,
        "days": len(day_rows),
        "entries": entries,
        "settingsPersisted": settings_row is not None,
        "latestUpdatedAt": latest.isoformat() if latest is not None else None,
    }
