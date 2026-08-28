from __future__ import annotations

import hashlib
import json
from pathlib import Path

import decision_ledger as dl
from database import db
import p37_verification
import tracker
import tracker_store

ROOT = Path(__file__).resolve().parents[1]


def _model_row(price: int = -105, *, line: float = 64.5, side: str = "over") -> dict:
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
        "line": line,
        "side": side,
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


def _stat_row(receiving_yards: float = 80.0) -> dict:
    return {
        "game_id": "p37-test-game",
        "player_id": "p37-test-player",
        "receiving_yards": receiving_yards,
        "receptions": 6,
        "receiving_tds": 1,
        "rushing_yards": 0,
        "rushing_tds": 0,
        "passing_yards": 0,
        "passing_tds": 0,
    }


def _clear_p37_tables() -> None:
    db.session.execute(dl.decision_ledger_receipts.delete())
    db.session.execute(tracker_store.tracker_day_snapshots.delete())
    db.session.execute(tracker_store.tracker_settings_snapshots.delete())
    db.session.commit()


def _silence_tracker_files(monkeypatch) -> None:
    monkeypatch.setattr(tracker, "_write_json_file", lambda *args, **kwargs: None)
    monkeypatch.setattr(tracker, "_read_json_file", lambda *args, **kwargs: {})


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


def test_receipt_normalizes_best_price_and_ignores_non_model_rows():
    row = _model_row()
    row.pop("price")
    row.pop("book")
    row["bestPrice"] = {"price": 110, "book": "BestBook"}
    receipt = dl.build_receipt(row, {"season_type": "REG"})
    assert receipt["release"]["price"] == 110
    assert receipt["release"]["book"] == "BestBook"

    manual = dict(row)
    manual["decisionGrade"] = "Pass"
    # The no-app-context path fails closed rather than recording Pass rows.
    result = dl.record_delivery([manual])
    assert result["candidates"] == 0
    assert result["inserted"] == 0


def test_decision_ledger_is_idempotent_and_never_rewrites_first_release(app_fixture):
    with app_fixture.app_context():
        _clear_p37_tables()
        context = {"season": 2026, "week": 1, "season_type": "REG", "source": "test"}
        first = dl.record_delivery([_model_row(-105)], context=context)
        second = dl.record_delivery([_model_row(115)], context=context)
        receipts = dl.list_receipts(limit=10, season=2026, week=1)

        assert first["inserted"] == 1
        assert second["inserted"] == 0
        assert second["existing"] == 1
        assert len(receipts) == 1
        assert receipts[0]["release"]["price"] == -105
        assert receipts[0]["grade"] == "pending"
        status = dl.ledger_status()
        assert status["receipts"] == 1
        assert status["pending"] == 1
        assert status["actionable"] == 1
        _clear_p37_tables()


def test_decision_ledger_grades_and_reports_calibration_and_roi(app_fixture, monkeypatch):
    with app_fixture.app_context():
        _clear_p37_tables()
        context = {"season": 2026, "week": 1, "season_type": "REG", "source": "test"}
        win = _model_row(-105, line=64.5, side="over")
        loss = _model_row(-110, line=90.5, side="over")
        loss["decisionGrade"] = "Strong Play"
        loss["consensusProb"] = 0.72
        push = _model_row(100, line=80.0, side="over")
        push["decisionGrade"] = "Lean"
        dl.record_delivery([win, loss, push], context=context)

        monkeypatch.setattr(
            dl.nfl_data,
            "get_schedule",
            lambda season: [{"game_id": "p37-test-game", "completed": True}],
        )
        monkeypatch.setattr(dl.nfl_data, "get_player_week_stats", lambda season: [_stat_row(80.0)])

        result = dl.grade_pending()
        assert result == {"graded": 3, "pending": 0, "available": True}
        receipts = dl.list_receipts(limit=10)
        grades = {receipt["release"]["line"]: receipt["grade"] for receipt in receipts}
        assert grades[64.5] == "win"
        assert grades[90.5] == "loss"
        assert grades[80.0] == "push"

        performance = dl.performance_summary()
        assert performance["available"] is True
        assert performance["receipts"] == 3
        assert performance["graded"] == 3
        assert performance["wins"] == 1
        assert performance["losses"] == 1
        assert performance["pushes"] == 1
        assert performance["calibrationSamples"] == 2
        assert 0 <= performance["brier"] <= 1
        assert 0 <= performance["ece"] <= 1
        assert performance["pricedGraded"] == 3
        assert performance["unitProfit"] is not None
        assert performance["unitRoi"] is not None
        assert performance["perDecisionGrade"]["Play"]["wins"] == 1
        assert performance["perDecisionGrade"]["Strong Play"]["losses"] == 1
        assert performance["perMarket"]["rec_yds"]["n"] == 3
        status = dl.ledger_status()
        assert status["graded"] == 3
        assert status["pending"] == 0
        _clear_p37_tables()


def test_ledger_grade_pending_handles_empty_state(app_fixture):
    with app_fixture.app_context():
        _clear_p37_tables()
        assert dl.grade_pending() == {"graded": 0, "pending": 0, "available": True}
        performance = dl.performance_summary()
        assert performance["receipts"] == 0
        assert performance["hitRate"] is None
        assert performance["brier"] is None
        assert performance["ece"] is None


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
        # Exercise update/upsert paths as well as first insert.
        source["2026-09-10"]["entries"][0]["grade"] = "win"
        assert tracker_store.save_store(source) is True
        assert tracker_store.load_store()["2026-09-10"]["entries"][0]["grade"] == "win"
        assert tracker_store.save_settings({"bankroll": 3000.0, "unit_pct": 0.02}) is True
        assert tracker_store.load_settings()["bankroll"] == 3000.0
        status = tracker_store.persistence_status()
        assert status["backend"] == "database"
        assert status["available"] is True
        assert status["days"] == 1
        assert status["entries"] == 1
        assert status["settingsPersisted"] is True
        assert status["latestUpdatedAt"] is not None
        assert tracker_store.save_store({}) is True
        assert tracker_store.load_store() == {}
        _clear_p37_tables()


def test_tracker_duplicate_save_preserves_first_release_evidence(app_fixture, monkeypatch):
    with app_fixture.app_context():
        _clear_p37_tables()
        _silence_tracker_files(monkeypatch)
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


def test_tracker_database_crud_grade_and_performance(app_fixture, monkeypatch):
    with app_fixture.app_context():
        _clear_p37_tables()
        _silence_tracker_files(monkeypatch)
        saved = tracker.add_pick({**_model_row(-105), "stakeDollars": 50})
        date = saved["gameday"]
        assert tracker.list_picks(date)[date]["entries"][0]["id"] == saved["id"]

        updated = tracker.update_pick(date, saved["id"], {"notes": "keep", "stakeDollars": 60})
        assert updated is not None
        assert updated["stakeDollars"] == 60
        assert tracker.update_pick(date, "missing", {"notes": "nope"}) is None

        monkeypatch.setattr(
            tracker.nfl_data,
            "get_schedule",
            lambda season: [{"game_id": "p37-test-game", "completed": True}],
        )
        monkeypatch.setattr(
            tracker.nfl_data,
            "get_player_week_stats",
            lambda season: [_stat_row(80.0)],
        )
        graded = tracker.grade_pending()
        assert graded["graded"] == 1
        summary = tracker.performance_summary()
        assert summary["wins"] == 1
        assert summary["losses"] == 0
        assert summary["hit_rate"] == 1.0
        assert summary["profitDollars"] > 0
        assert summary["roi"] > 0
        assert 0 <= summary["brier"] <= 1
        assert 0 <= summary["ece"] <= 1
        assert summary["receiptCoverage"] == 1.0
        assert summary["persistence"]["backend"] == "database"
        assert tracker.delete_pick(date, saved["id"]) is True
        assert tracker.delete_pick(date, saved["id"]) is False
        _clear_p37_tables()


def test_tracker_settings_persist_without_local_files(app_fixture, monkeypatch):
    with app_fixture.app_context():
        _clear_p37_tables()
        _silence_tracker_files(monkeypatch)
        saved = tracker.save_settings(
            {"bankroll": "2400", "unit_pct": 0.02, "kelly_fraction": 0.5, "max_bet_pct": 0.08}
        )
        assert saved["bankroll"] == 2400.0
        assert tracker.get_settings()["kelly_fraction"] == 0.5
        status = tracker.persistence_status()
        assert status["settingsPersisted"] is True
        _clear_p37_tables()


def test_p37_readiness_snapshot_is_read_only_and_green(app_fixture):
    with app_fixture.app_context():
        _clear_p37_tables()
        snapshot = p37_verification.readiness_snapshot()
        assert snapshot["ok"] is True
        assert snapshot["mode"] == "read-only"
        assert all(snapshot["gates"].values())
        assert snapshot["trackerPersistence"]["backend"] == "database"
        assert snapshot["publicationLedger"]["backend"] == "database"
        assert snapshot["sampledReceipts"] == 0
        synthetic = snapshot["syntheticReceiptContract"]
        assert synthetic["receiptIdStableAcrossLaterPriceMovement"] is True
        assert synthetic["fingerprintChangesWhenReleasePayloadChanges"] is True


def test_p37_tracker_routes_expose_persistence_and_ledger(client, app_fixture):
    with app_fixture.app_context():
        _clear_p37_tables()
    response = client.get("/api/tracker/persistence")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["tracker"]["backend"] == "database"
    assert payload["publicationLedger"]["backend"] == "database"

    response = client.get("/api/tracker/ledger/performance")
    assert response.status_code == 200
    assert response.get_json()["available"] is True

    response = client.get("/api/tracker/ledger?season=2026&week=1&limit=5")
    assert response.status_code == 200
    assert response.get_json()["receipts"] == []


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
