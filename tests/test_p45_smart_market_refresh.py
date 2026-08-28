from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import p45_smart_market_refresh as p45

ROOT = Path(__file__).resolve().parents[1]


def _game(game_id: str, kickoff: datetime, *, week: int = 1, stype: str = "REG") -> dict:
    return {
        "game_id": game_id,
        "season": 2026,
        "season_type": stype,
        "week": week,
        "date": kickoff.isoformat(),
        "completed": False,
    }


def _market(
    *,
    actionable: bool = False,
    quote_status: str = "stale",
    grade: str = "Play",
    edge: float | None = 0.06,
    ev: float | None = 0.08,
) -> dict:
    return {
        "gameId": "g1",
        "market": "moneyline",
        "pickLabel": "AAA ML",
        "selectedSide": "home",
        "selectedTeam": "AAA",
        "modelProbability": 0.64,
        "confidenceScore": 76.0,
        "decisionGrade": grade,
        "quoteStatus": quote_status,
        "priceStatus": "positive_value" if edge and edge > 0 else "no_value",
        "fairMarketProbability": 0.56 if quote_status != "unpriced" else None,
        "referenceProbability": 0.56 if quote_status != "unpriced" else None,
        "edge": edge,
        "evPct": ev,
        "kellyPct": 0.02,
        "freshBookCount": 4 if quote_status == "fresh" else 0,
        "pairedFairBookCount": 3 if quote_status == "fresh" else 0,
        "bestBook": "Book A" if quote_status != "unpriced" else None,
        "bestPrice": -105 if quote_status != "unpriced" else None,
        "actionable": actionable,
    }


def test_next_upcoming_slate_ignores_finished_preseason_marker(monkeypatch):
    now = datetime(2026, 8, 28, 20, 0, tzinfo=UTC)
    schedule = [
        {**_game("pre", now - timedelta(days=2), week=3, stype="PRE"), "completed": True},
        _game("reg1", now + timedelta(days=12), week=1, stype="REG"),
        _game("reg2", now + timedelta(days=12, hours=3), week=1, stype="REG"),
    ]
    monkeypatch.setattr(p45.nfl_data, "get_schedule", lambda season: schedule)
    slate = p45.next_upcoming_slate(2026, now=now)
    assert slate is not None
    assert slate["seasonType"] == "REG"
    assert slate["week"] == 1
    assert slate["gameCount"] == 2


def test_refresh_cadence_enters_standby_outside_horizon(monkeypatch):
    monkeypatch.setenv("P45_REFRESH_HORIZON_HOURS", "168")
    assert p45.cadence_seconds(200) is None
    assert p45.cadence_seconds(100) == 120 * 60
    assert p45.cadence_seconds(48) == 30 * 60
    assert p45.cadence_seconds(12) == 10 * 60
    assert p45.cadence_seconds(3) == 5 * 60


def test_refresh_if_due_never_spends_without_explicit_permission(monkeypatch):
    monkeypatch.setenv("ENABLE_GAME_MARKET_REFRESH", "true")
    monkeypatch.setattr(
        p45,
        "refresh_status",
        lambda *args, **kwargs: {
            "available": True,
            "enabled": True,
            "due": True,
            "state": "due-stale",
            "slate": {"season": 2026, "seasonType": "REG", "week": 1},
        },
    )
    called = []
    monkeypatch.setattr(p45.p42, "hydrate_week", lambda *args, **kwargs: called.append(1))
    result = p45.refresh_next_slate(2026, allow_provider_spend=False)
    assert result["action"] == "blocked"
    assert result["providerRequests"] == 0
    assert called == []


def test_due_refresh_uses_bounded_targeted_request_cap(monkeypatch):
    monkeypatch.setenv("ENABLE_GAME_MARKET_REFRESH", "true")
    monkeypatch.setenv("P45_MAX_TARGETED_REQUESTS", "2")
    monkeypatch.setattr(
        p45,
        "refresh_status",
        lambda *args, **kwargs: {
            "available": True,
            "enabled": True,
            "due": True,
            "state": "due-stale",
            "slate": {"season": 2026, "seasonType": "REG", "week": 1},
        },
    )
    calls = []

    def _hydrate(*args, **kwargs):
        calls.append(kwargs)
        return {"ok": True, "providerRequests": 1}

    monkeypatch.setattr(p45.p42, "hydrate_week", _hydrate)
    result = p45.refresh_next_slate(2026, allow_provider_spend=True)
    assert result["state"] == "refreshed"
    assert result["providerRequests"] == 1
    assert calls[0]["max_targeted_requests"] == 2


def test_opportunity_continuity_keeps_stale_play_visible_but_not_actionable():
    delivery = {
        "modelVersion": "p43-decision-board-v1",
        "summary": {},
        "safety": {"cacheOnly": True},
        "allMarkets": [_market(actionable=False, quote_status="stale")],
    }
    out = p45.enrich_delivery(delivery)
    assert out["state"] == "refresh-needed"
    assert out["opportunities"][0]["opportunityState"] == "REFRESH"
    assert out["opportunities"][0]["recommendedAction"] == "REFRESH PRICE"
    assert out["opportunities"][0]["actionable"] is False
    assert "quote_not_fresh" in out["opportunities"][0]["actionBlockers"]
    assert p45.verify_opportunity_contract(out)["ok"] is True


def test_model_only_lean_remains_visible_without_fake_price():
    delivery = {
        "modelVersion": "p43-decision-board-v1",
        "summary": {},
        "safety": {"cacheOnly": True},
        "allMarkets": [
            _market(
                actionable=False,
                quote_status="unpriced",
                grade="Lean",
                edge=None,
                ev=None,
            )
        ],
    }
    out = p45.enrich_delivery(delivery)
    item = out["opportunities"][0]
    assert out["state"] == "model-opportunities"
    assert item["opportunityState"] == "MODEL"
    assert item["bestPrice"] is None
    assert item["actionable"] is False


def test_actionable_pick_is_preserved_not_recomputed():
    delivery = {
        "modelVersion": "p43-decision-board-v1",
        "summary": {},
        "safety": {"cacheOnly": True},
        "allMarkets": [_market(actionable=True, quote_status="fresh")],
    }
    out = p45.enrich_delivery(delivery)
    item = out["opportunities"][0]
    assert item["opportunityState"] == "ACTIONABLE"
    assert item["recommendedAction"] == "BET"
    assert item["actionBlockers"] == []


def test_p45_scheduler_and_product_surface_are_wired():
    scheduler = (ROOT / "scheduled_jobs.py").read_text(encoding="utf-8")
    fly = (ROOT / "fly.toml").read_text(encoding="utf-8")
    games = (ROOT / "games.html").read_text(encoding="utf-8")
    routes = (ROOT / "routes" / "games.py").read_text(encoding="utf-8")
    verifier = (ROOT / "scripts" / "p45_smart_market_refresh_verification.py").read_text(
        encoding="utf-8"
    )
    assert '"game-market-refresh"' in scheduler
    assert "refresh_next_slate(allow_provider_spend=True)" in scheduler
    assert "ENABLE_GAME_MARKET_REFRESH = 'true'" in fly
    assert "/api/game-opportunities/week" in games
    assert "/api/game-opportunities/week" in routes
    assert "/api/game-market-refresh/status" in routes
    assert (
        "scheduler_job_registered = scheduler_row is not None and bool(scheduler_row.enabled)"
        in verifier
    )
    assert '"scheduler_job_registered": scheduler_job_registered' in verifier
    assert 'target_week = int(slate["week"])' in verifier
    assert 'target_type = str(slate["seasonType"]).upper()' in verifier
    assert '(target_type == "PRE" and target_week >= 0)' in verifier
    assert '"opportunity_board_matches_next_slate"' in verifier
    assert '"opportunity_board_has_full_game_coverage"' in verifier
    assert "covered_game_count == game_count" in verifier
    assert '"opportunity_board_state_is_consistent"' in verifier
    assert "opportunity_board_has_useful_model_pool" not in verifier
    assert "next_slate_is_2026_reg_week_one" not in verifier
