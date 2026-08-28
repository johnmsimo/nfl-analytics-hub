from __future__ import annotations

import hashlib
import json
from pathlib import Path

import sqlalchemy as sa

import decision_ledger as dl
from database import db
import tracker
import tracker_store

ROOT = Path(__file__).resolve().parents[1]


def _model_row(price: int = -105) -> dict:
    return {
        "gameId": "p37-test-game",
        "season": 2026,
        "week": 1,
        "gameday": "2026-09-10",
        "player": "Ledger Test Player",
        "playerId": "p37-test-player",
        "team": "AAA",
        "opponent": "BBB",
        "position": "WR",
        "marketKey": "rec_yds",
        "marketLabel": "Rec Yards",
        "line": 64.5,
        "side": "over",
        "price": price,
        "book": "TestBook",
        "modelMean": 71.0,
        "modelProb": 0.61,
        "consensusProb": 0.63,
        "simulationProb": 0.64,
        "simulationAgreement": 0.92,
        "confidenceScore": 0.79,
        "confidenceGrade": "high",
        "matchupGrade": "favorable",
        "decisionGrade": "Play",
        "decisionScore": 0.68,
        "priceStatus": "positive_value",
        "quoteStatus": "fresh",
        "edge": 0.04,
        "evPct": 0.06,
        "kellyPct": 0.01,
        "actionable": True,
        "modelSource": "p3.6-live-market-actionability",
        "decisionModelVersion": "p3.4-simulation-decision",
    }


def _clear_p37_tables() -> None:
    db.session.execute(dl.decision_ledger_receipts.delete())
    db.session.execute(tracker_store.tracker_day_snapshots.delete())
    db.session.execute(tracker_store.tracker_settings_snapshots.delete())
    db.session.commit()


def test_p37_migration_is_chained_after_p36():
    migration = (
        ROOT / "migrations" / "versions" / "20260828_p37_decision_ledger.py"
    ).read_text(encoding="utf-8")

    assert 'revision = "20260828_p37_ledger"' in migration
    assert 'down_revision = "20260828_p36_cache"' in migration
    assert '"tracker_day_snapshots"' in migration
    assert '"tracker_settings_snapshots"' in migration
    assert '"decision_ledger_receipts"' in migration


def test_release_receipt_identity_is_stable_but_payload_fingerprint_is_immutable():
    context = {"season": 2026, "week": 1, "season_type": "REG", "source": "test"}
    first = dl.build_receipt(_model_row(-105), context)
    second = dl.build_receipt(_model_row(115), context)

    assert first["receiptId"] == second["receiptId"]
    assert first["releaseKey"] == second["releaseKey"]
    assert first["releaseFingerprint"] != second["releaseFingerprint"]
    expected = hashlib.sha256(
        json.dumps(
            first["release"], sort_keys=True, separators=(",", ":"), default=str
        ).encode("utf-8")
    ).hexdigest()
    assert first["releaseFingerprint"] == expected
    assert first["release"]["price"] == -105
    assert first["release"]["decisionGrade"] == "Play"
    assert first["release"]["actionable"] is True


def test_decision_ledger_is_idempotent_and_never_rewrites_first_release(app_fixture):
    with app_fixture.app_context():
        _clear_p37_tables()
        context = {"season": 2026, "week": 1, "season_type": "REG", "source": "test"}
        first = dl.record_delivery([_model_row(-105)], context=context)
        second = dl.record_delivery([_model_row(115)], context=context)
        receipts = dl.list_receipts(limit=10)

        assert first["inserted"] == 1
        assert second["inserted"] == 0
        assert second["existing"] == 1
        assert len(receipts) == 1
        assert receipts[0]["release"]["price"] == -105
        assert receipts[0]["grade"] == "pending"
        assert dl.ledger_status()["receipts"] == 1
        _clear_p37_tables()


def test_tracker_store_round_trips_days_and_settings_in_database(app_fixture):
    with app_fixture.app_context():
        _clear_p37_tables()
        source = {
            "2026-09-10": {
                "entries": [{"id": "pick-1", "marketKey": "rec_yds", "grade": "pending"}],
                "closingAttempted": ["game-1"],
            }
        }
        assert tracker_store.save_store(source) is True
        assert tracker_store.load_store() == source
        assert tracker_store.save_settings({"bankroll": 2500.0, "unit_pct": 0.01}) is True
        assert tracker_store.load_settings()["bankroll"] == 2500.0
        status = tracker_store.persistence_status()
        assert status["backend"] == "database"
        assert status["available"] is True
        assert status["days"] == 1
        assert status["entries"] == 1
        assert status["settingsPersisted"] is True
        _clear_p37_tables()


def test_tracker_duplicate_save_preserves_first_release_evidence(app_fixture, monkeypatch):
    with app_fixture.app_context():
        _clear_p37_tables()
        monkeypatch.setattr(tracker, "_write_json_file", lambda *args, **kwargs: None)
        monkeypatch.setattr(tracker, "_read_json_file", lambda *args, **kwargs: {})
        first_payload = {**_model_row(-105), "stakeDollars": 25}
        second_payload = {**_model_row(120), "stakeDollars": 40, "edge": 0.09}

        first = tracker.add_pick(first_payload)
        second = tracker.add_pick(second_payload)

        assert second["id"] == first["id"]
        assert second["price"] == -105
        assert second["edge"] == 0.04
        assert second["releaseFingerprint"] == first["releaseFingerprint"]
        assert second["stakeDollars"] == 40
        status = tracker.persistence_status()
        assert status["backend"] == "database"
        assert status["entries"] == 1
        _clear_p37_tables()


def test_p37_verification_is_read_only_and_no_provider_refresh():
    workflow = (
        ROOT / ".github" / "workflows" / "p37-decision-ledger-verification.yml"
    ).read_text(encoding="utf-8")
    verification = (ROOT / "p37_verification.py").read_text(encoding="utf-8")

    assert "RUN_DECISION_LEDGER_VERIFY" in workflow
    assert "environment: production" in workflow
    assert "/app/scripts/p37_decision_ledger_verification.py" in workflow
    assert "/app/scripts/p2_exit_verification.py" in workflow
    assert "RUN_ONE_EVENT" not in workflow
    assert "refresh_game_props" not in workflow
    assert "fetch_event_odds_live" not in workflow
    assert "odds_api" not in verification
