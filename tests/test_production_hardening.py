from __future__ import annotations

from unittest.mock import Mock

import scheduled_jobs
from app import app
from database import _should_create_schema, db


def test_production_does_not_auto_create_schema(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv("AUTO_CREATE_SCHEMA", raising=False)
    assert _should_create_schema() is False


def test_explicit_schema_creation_override(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("AUTO_CREATE_SCHEMA", "true")
    assert _should_create_schema() is True


def test_optional_scheduler_jobs_are_disabled_by_default(monkeypatch):
    monkeypatch.delenv("ENABLE_EXTERNAL_SYNC", raising=False)
    monkeypatch.delenv("ENABLE_COMMERCIAL_SYNC", raising=False)
    assert scheduled_jobs._job_enabled("external-data-sync") is False
    assert scheduled_jobs._job_enabled("commercial-data-sync") is False
    assert scheduled_jobs._job_enabled("cached-data-sync") is True


def test_ready_is_public_and_sanitizes_database_errors(client, monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    with client.session_transaction() as session:
        session.clear()

    execute = Mock(side_effect=RuntimeError("postgresql://secret@database/internal"))
    monkeypatch.setattr(db.session, "execute", execute)

    response = client.get("/ready")
    assert response.status_code == 503
    assert response.get_json() == {"ok": False, "database": "unavailable"}
