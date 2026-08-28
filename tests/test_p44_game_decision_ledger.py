from __future__ import annotations

from pathlib import Path

from database import db
import p44_game_decision_ledger as p44

ROOT = Path(__file__).resolve().parents[1]


def _pick(
    market: str = "moneyline",
    *,
    side: str = "home",
    line: float | None = None,
    price: int = -110,
    actionable: bool = True,
    quote_status: str = "fresh",
) -> dict:
    return {
        "gameId": "p44-game",
        "season": 2026,
        "seasonType": "REG",
        "week": 1,
        "kickoffAt": "2026-09-10T00:00:00Z",
        "homeTeam": "AAA",
        "awayTeam": "BBB",
        "market": market,
        "marketLabel": market.title(),
        "pickLabel": f"{side} {market}",
        "selectedSide": side,
        "selectedTeam": "AAA" if side == "home" else ("BBB" if side == "away" else None),
        "line": line,
        "modelProbability": 0.64,
        "confidenceScore": 77.0,
        "decisionGrade": "Play",
        "fairMarketProbability": 0.56,
        "referenceProbability": 0.56,
        "edge": 0.08,
        "evPct": 0.07,
        "kellyPct": 0.02,
        "bestBook": "TestBook",
        "bestPrice": price,
        "quoteAt": "2026-08-28T19:00:00+00:00",
        "quoteAgeSeconds": 20.0,
        "freshBookCount": 5,
        "pairedFairBookCount": 4,
        "quoteStatus": quote_status,
        "priceStatus": "positive_value",
        "actionable": actionable,
        "reasons": ["team-strength edge"],
        "risks": ["normal variance"],
        "hydratedAt": "2026-08-28T19:00:00+00:00",
        "sourceModelVersion": "p42-hydration-v1",
    }


def _clear() -> None:
    db.session.execute(p44.game_decision_ledger_receipts.delete())
    db.session.commit()


def test_p44_migration_is_chained_after_p37():
    migration = (
        ROOT / "migrations" / "versions" / "20260828_p44_game_decision_ledger.py"
    ).read_text(encoding="utf-8")
    assert 'revision = "20260828_p44_game_ledger"' in migration
    assert 'down_revision = "20260828_p37_ledger"' in migration
    assert '"game_decision_ledger_receipts"' in migration


def test_game_receipt_identity_ignores_later_price_movement_but_fingerprint_changes():
    first = p44.build_receipt(_pick(price=-110))
    second = p44.build_receipt(_pick(price=120))
    assert first["receiptId"] == second["receiptId"]
    assert first["releaseKey"] == second["releaseKey"]
    assert first["releaseFingerprint"] != second["releaseFingerprint"]
    assert first["release"]["bestPrice"] == -110
    assert first["release"]["publicationSource"] == "p4.3-game-decision-board"


def test_record_delivery_requires_upstream_actionable_and_fresh(app_fixture):
    with app_fixture.app_context():
        _clear()
        result = p44.record_delivery(
            [
                _pick(actionable=False),
                _pick(market="spread", line=-3.5, quote_status="stale"),
            ]
        )
        assert result["candidates"] == 0
        assert p44.ledger_status()["receipts"] == 0
        _clear()


def test_game_ledger_is_idempotent_and_preserves_first_release(app_fixture):
    with app_fixture.app_context():
        _clear()
        first = p44.record_delivery([_pick(price=-110)])
        second = p44.record_delivery([_pick(price=125)])
        receipts = p44.list_receipts(limit=10, season=2026, week=1)
        assert first["inserted"] == 1
        assert second["inserted"] == 0
        assert second["existing"] == 1
        assert len(receipts) == 1
        assert receipts[0]["release"]["bestPrice"] == -110
        assert receipts[0]["grade"] == "pending"
        assert p44.ledger_status()["isolatedFromPlayerPropLedger"] is True
        _clear()


def test_moneyline_grading():
    release = p44.build_receipt(_pick())["release"]
    assert p44.grade_market_release(release, home_score=27, away_score=20)[0] == "win"
    assert p44.grade_market_release(release, home_score=17, away_score=24)[0] == "loss"
    assert p44.grade_market_release(release, home_score=20, away_score=20)[0] == "push"


def test_spread_grading_for_selected_side_line():
    release = p44.build_receipt(_pick("spread", side="home", line=-3.5))["release"]
    assert p44.grade_market_release(release, home_score=27, away_score=20)[0] == "win"
    assert p44.grade_market_release(release, home_score=23, away_score=21)[0] == "loss"
    push = p44.build_receipt(_pick("spread", side="away", line=3.0))["release"]
    assert p44.grade_market_release(push, home_score=24, away_score=21)[0] == "push"


def test_total_grading():
    over = p44.build_receipt(_pick("total", side="over", line=44.5))["release"]
    under = p44.build_receipt(_pick("total", side="under", line=44.5))["release"]
    push = p44.build_receipt(_pick("total", side="over", line=47.0))["release"]
    assert p44.grade_market_release(over, home_score=27, away_score=20)[0] == "win"
    assert p44.grade_market_release(under, home_score=27, away_score=20)[0] == "loss"
    assert p44.grade_market_release(push, home_score=27, away_score=20)[0] == "push"


def test_grade_pending_and_performance(app_fixture, monkeypatch):
    with app_fixture.app_context():
        _clear()
        p44.record_delivery(
            [
                _pick("moneyline", side="home", price=-110),
                _pick("spread", side="away", line=3.5, price=100),
                _pick("total", side="over", line=44.5, price=105),
            ]
        )
        monkeypatch.setattr(
            p44.nfl_data,
            "get_schedule",
            lambda season: [
                {
                    "game_id": "p44-game",
                    "completed": True,
                    "home_score": 24,
                    "away_score": 21,
                }
            ],
        )
        result = p44.grade_pending()
        assert result == {"available": True, "graded": 3, "pending": 0}
        performance = p44.performance_summary()
        assert performance["receipts"] == 3
        assert performance["graded"] == 3
        assert performance["wins"] == 3
        assert performance["hitRate"] == 1.0
        assert performance["pricedGraded"] == 3
        assert performance["unitProfit"] is not None
        assert 0 <= performance["brier"] <= 1
        assert performance["perMarket"]["moneyline"]["wins"] == 1
        assert performance["perMarket"]["spread"]["wins"] == 1
        assert performance["perMarket"]["total"]["wins"] == 1
        _clear()


def test_publish_week_delivery_records_only_p43_picks(app_fixture, monkeypatch):
    delivery = {
        "available": True,
        "season": 2026,
        "seasonType": "REG",
        "week": 1,
        "picks": [_pick()],
        "watchlist": [_pick(actionable=False)],
        "allMarkets": [_pick(), _pick(actionable=False)],
    }
    monkeypatch.setattr(p44.p43, "build_week_delivery", lambda *args, **kwargs: delivery)
    with app_fixture.app_context():
        _clear()
        result = p44.publish_week_delivery(2026, 1, "REG")
        assert result["publication"]["candidates"] == 1
        assert result["publication"]["inserted"] == 1
        assert p44.ledger_status()["receipts"] == 1
        _clear()


def test_p44_tracker_routes_expose_game_ledger(client, app_fixture):
    with app_fixture.app_context():
        _clear()
    response = client.get("/api/tracker/persistence")
    assert response.status_code == 200
    assert response.get_json()["gameDecisionLedger"]["backend"] == "database"
    response = client.get("/api/tracker/game-ledger/performance")
    assert response.status_code == 200
    assert response.get_json()["available"] is True
    response = client.get("/api/tracker/game-ledger?season=2026&week=1&limit=5")
    assert response.status_code == 200
    assert response.get_json()["receipts"] == []


def test_p44_scheduler_registers_automatic_game_grading():
    scheduler = (ROOT / "scheduled_jobs.py").read_text(encoding="utf-8")
    assert '"game-decision-grading"' in scheduler
    assert "grade_pending()" in scheduler
