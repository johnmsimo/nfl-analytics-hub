from __future__ import annotations

from pathlib import Path

import odds_api
import provider_cache_store as pcs

ROOT = Path(__file__).resolve().parents[1]


def _snapshot() -> dict:
    return {
        "game_odds": {
            "date": "2026-08-28",
            "fetched_at": 1000.0,
            "events": [{"id": "evt-1"}, {"id": "evt-2"}],
        },
        "event_props": {
            "evt-1": {
                "date": "2026-08-28",
                "fetched_at": 1001.0,
                "data": {"bookmakers": []},
            },
            "evt-2": {
                "date": "2026-08-28",
                "fetched_at": 1002.0,
                "data": {"bookmakers": [{"key": "book-a"}]},
            },
        },
    }


def test_provider_cache_flatten_inflate_round_trip():
    source = _snapshot()

    records = pcs.flatten_snapshot(source)
    rebuilt = pcs.inflate_records(records)

    assert set(records) == {"game_odds", "event_props:evt-1", "event_props:evt-2"}
    assert rebuilt == source


def test_odds_snapshot_prefers_durable_database_cache(monkeypatch):
    durable = _snapshot()
    mirrored: list[dict] = []
    monkeypatch.setattr(odds_api, "_snapshot", None)
    monkeypatch.setattr(odds_api.provider_cache_store, "load_snapshot", lambda provider: durable)
    monkeypatch.setattr(odds_api, "_save_file_snapshot", lambda snapshot: mirrored.append(snapshot))

    loaded = odds_api._load_snapshot()

    assert loaded == durable
    assert odds_api.peek_game_odds() == durable["game_odds"]["events"]
    assert mirrored == [durable]


def test_existing_local_cache_bootstraps_durable_store(monkeypatch):
    local = _snapshot()
    persisted: list[tuple[str, dict]] = []
    monkeypatch.setattr(odds_api, "_snapshot", None)
    monkeypatch.setattr(odds_api.provider_cache_store, "load_snapshot", lambda provider: {})
    monkeypatch.setattr(odds_api, "_load_file_snapshot", lambda: local)
    monkeypatch.setattr(
        odds_api.provider_cache_store,
        "save_snapshot",
        lambda provider, snapshot: persisted.append((provider, snapshot)) or True,
    )

    loaded = odds_api._load_snapshot()

    assert loaded == local
    assert persisted == [(odds_api.PROVIDER_KEY, local)]


def test_save_snapshot_writes_database_and_local_mirror(monkeypatch):
    source = _snapshot()
    durable: list[tuple[str, dict]] = []
    mirrored: list[dict] = []
    monkeypatch.setattr(odds_api, "_snapshot", source)
    monkeypatch.setattr(
        odds_api.provider_cache_store,
        "save_snapshot",
        lambda provider, snapshot: durable.append((provider, snapshot)) or True,
    )
    monkeypatch.setattr(odds_api, "_save_file_snapshot", lambda snapshot: mirrored.append(snapshot))

    odds_api._save_snapshot()

    assert durable == [(odds_api.PROVIDER_KEY, source)]
    assert mirrored == [source]


def test_snapshot_status_exposes_persistence_backend(monkeypatch):
    monkeypatch.setattr(odds_api, "_snapshot", _snapshot())
    monkeypatch.setattr(
        odds_api.provider_cache_store,
        "cache_status",
        lambda provider: {
            "backend": "database",
            "available": True,
            "rows": 3,
            "gameCatalogRows": 1,
            "eventPropRows": 2,
            "latestUpdatedAt": "2026-08-28T15:00:00+00:00",
        },
    )

    status = odds_api.snapshot_status()

    assert status["cache_persistence"]["backend"] == "database"
    assert status["cache_persistence"]["available"] is True
    assert status["cache_persistence"]["gameCatalogRows"] == 1
    assert status["cache_persistence"]["eventPropRows"] == 2


def test_provider_cache_migration_is_chained_after_p21():
    migration = (
        ROOT / "migrations" / "versions" / "20260828_p36_provider_cache.py"
    ).read_text(encoding="utf-8")

    assert 'revision = "20260828_p36_cache"' in migration
    assert 'down_revision = "20260825_p21"' in migration
    assert '"provider_cache_snapshots"' in migration
