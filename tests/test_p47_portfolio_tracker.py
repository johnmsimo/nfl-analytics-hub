from __future__ import annotations

from pathlib import Path

import p47_portfolio_tracker as p47


def _row(
    game_id: str = "g1",
    market: str = "moneyline",
    side: str = "home",
    line: float | None = None,
) -> dict:
    return {
        "gameId": game_id,
        "season": 2026,
        "seasonType": "REG",
        "week": 1,
        "kickoffAt": "2026-09-13T17:00:00+00:00",
        "homeTeam": "Home Team",
        "awayTeam": "Away Team",
        "market": market,
        "marketLabel": market.title(),
        "pickLabel": f"Home Team {market}",
        "selectedSide": side,
        "selectedTeam": "Home Team" if side == "home" else None,
        "line": line,
        "modelProbability": 0.62,
        "confidenceScore": 82.0,
        "decisionGrade": "Strong Play",
        "quoteStatus": "fresh",
        "priceStatus": "positive_value",
        "fairMarketProbability": 0.54,
        "referenceProbability": 0.55,
        "edge": 0.08,
        "evPct": 0.10,
        "kellyPct": 0.05,
        "freshBookCount": 4,
        "pairedFairBookCount": 3,
        "bestBook": "Book A",
        "bestPrice": -105,
        "quoteAt": "2026-09-13T16:58:00+00:00",
        "quoteAgeSeconds": 30,
        "actionable": True,
        "opportunityState": "ACTIONABLE",
        "portfolioEligible": True,
        "requestedStakePct": 0.025,
        "requestedStakeDollars": 25.0,
        "recommendedStakePct": 0.025,
        "recommendedStakeDollars": 25.0,
        "recommendedStakeUnits": 2.5,
        "reasons": ["Model edge 8.0% vs de-vig market"],
        "risks": ["Price can move before kickoff"],
    }


def _report(rows: list[dict] | None = None) -> dict:
    rows = rows if rows is not None else [_row()]
    return {
        "available": True,
        "modelVersion": "p46-bankroll-portfolio-v1",
        "season": 2026,
        "seasonType": "REG",
        "week": 1,
        "portfolio": rows,
        "summary": {"portfolioPicks": len(rows)},
    }


def test_moneyline_maps_to_tracker_game_market_and_preserves_stake():
    payload = p47.to_tracker_payload(_row(), _report())
    assert payload["marketKey"] == "h2h"
    assert payload["side"] == "home"
    assert payload["gameday"] == "2026-09-13"
    assert payload["book"] == "Book A"
    assert payload["price"] == -105
    assert payload["stakeDollars"] == 25.0
    assert payload["stakeUnits"] == 2.5
    assert payload["modelProb"] == 0.62
    assert payload["fairMarketProb"] == 0.54
    assert payload["source"] == p47.MODEL_NAME
    assert payload["actionable"] is True


def test_total_maps_to_tracker_total_without_inventing_team():
    row = _row(market="total", side="over", line=47.5)
    payload = p47.to_tracker_payload(row, _report([row]))
    assert payload["marketKey"] == "total"
    assert payload["side"] == "over"
    assert payload["line"] == 47.5
    assert payload["team"] is None


def test_non_actionable_row_cannot_be_converted_to_tracker_pick():
    row = _row()
    row.update({"portfolioEligible": False, "actionable": False, "opportunityState": "WATCH"})
    try:
        p47.to_tracker_payload(row, _report([row]))
    except ValueError as exc:
        assert "not eligible" in str(exc)
    else:
        raise AssertionError("non-actionable row unexpectedly converted")


def test_tracking_key_binds_exact_displayed_price_and_allocation():
    original = _row()
    price_changed = dict(original)
    price_changed["bestPrice"] = -110
    stake_changed = dict(original)
    stake_changed["recommendedStakeDollars"] = 20.0
    assert p47.tracking_key(original) != p47.tracking_key(price_changed)
    assert p47.tracking_key(original) != p47.tracking_key(stake_changed)


def test_stale_displayed_confirmation_key_fails_closed_after_row_changes(monkeypatch):
    displayed = _row()
    stale_key = p47.tracking_key(displayed)
    rebuilt = dict(displayed)
    rebuilt["bestBook"] = "Book B"
    rebuilt["bestPrice"] = -110
    called = []
    monkeypatch.setattr(p47.tracker, "add_pick", lambda payload: called.append(payload))
    result = p47.confirm_portfolio_from_report(
        _report([rebuilt]),
        confirmed=True,
        selection_keys=[stale_key],
        persist=True,
    )
    assert result["ok"] is False
    assert result["error"] == "unknown_portfolio_selection"
    assert called == []


def test_tracking_status_is_read_only_and_marks_existing_pick(monkeypatch):
    row = _row()
    store = {
        "2026-09-13": {
            "entries": [
                {
                    "gameId": "g1",
                    "marketKey": "h2h",
                    "line": None,
                    "side": "home",
                }
            ]
        }
    }
    called = []
    monkeypatch.setattr(p47.tracker, "add_pick", lambda payload: called.append(payload))
    status = p47.build_tracking_status_from_portfolio(_report([row]), tracked_store=store)
    assert status["state"] == "all-tracked"
    assert status["summary"]["trackedPicks"] == 1
    assert status["rows"][0]["tracked"] is True
    assert status["safety"]["trackerWrite"] is False
    assert status["safety"]["confirmationBindsExactAllocation"] is True
    assert called == []


def test_explicit_confirmation_is_required_before_any_tracker_write(monkeypatch):
    called = []
    monkeypatch.setattr(p47.tracker, "add_pick", lambda payload: called.append(payload))
    result = p47.confirm_portfolio_from_report(_report(), confirmed=False)
    assert result["ok"] is False
    assert result["error"] == "explicit_confirmation_required"
    assert result["saved"] == 0
    assert result["safety"]["trackerWrite"] is False
    assert called == []


def test_dry_run_verifies_confirmation_path_without_persistence(monkeypatch):
    monkeypatch.setattr(
        p47.tracker,
        "add_pick",
        lambda payload: (_ for _ in ()).throw(AssertionError("dry-run wrote Tracker")),
    )
    result = p47.confirm_portfolio_from_report(_report(), confirmed=True, persist=False)
    assert result["ok"] is True
    assert result["mode"] == "dry-run"
    assert result["planned"] == 1
    assert result["saved"] == 0
    assert result["safety"]["trackerWrite"] is False


def test_explicit_empty_selection_tracks_nothing(monkeypatch):
    called = []
    monkeypatch.setattr(p47.tracker, "list_picks", lambda: {})
    monkeypatch.setattr(p47.tracker, "add_pick", lambda payload: called.append(payload))
    result = p47.confirm_portfolio_from_report(
        _report(),
        confirmed=True,
        selection_keys=[],
        persist=True,
    )
    assert result["ok"] is True
    assert result["planned"] == 0
    assert result["saved"] == 0
    assert result["existing"] == 0
    assert called == []


def test_confirmed_subset_saves_only_requested_portfolio_row(monkeypatch):
    first = _row("g1")
    second = _row("g2", market="spread", side="away", line=3.5)
    second["selectedTeam"] = "Away Team"
    report = _report([first, second])
    saved_payloads = []

    monkeypatch.setattr(p47.tracker, "list_picks", lambda: {})

    def _save(payload):
        saved_payloads.append(payload)
        return {**payload, "id": "receipt-1", "releaseFingerprint": "abc123"}

    monkeypatch.setattr(p47.tracker, "add_pick", _save)
    key = p47.tracking_key(second)
    result = p47.confirm_portfolio_from_report(
        report,
        confirmed=True,
        selection_keys=[key],
        persist=True,
    )
    assert result["ok"] is True
    assert result["saved"] == 1
    assert result["planned"] == 1
    assert len(saved_payloads) == 1
    assert saved_payloads[0]["gameId"] == "g2"
    assert saved_payloads[0]["marketKey"] == "spread"
    assert result["safety"]["automaticBetPlacement"] is False
    assert result["safety"]["sportsbookExecution"] is False


def test_unknown_selection_fails_closed_without_tracker_write(monkeypatch):
    called = []
    monkeypatch.setattr(p47.tracker, "add_pick", lambda payload: called.append(payload))
    result = p47.confirm_portfolio_from_report(
        _report(),
        confirmed=True,
        selection_keys=["not-a-current-portfolio-row"],
        persist=True,
    )
    assert result["ok"] is False
    assert result["error"] == "unknown_portfolio_selection"
    assert called == []


def test_p47_routes_and_user_surface_are_wired():
    root = Path(__file__).resolve().parents[1]
    routes = (root / "routes" / "games.py").read_text(encoding="utf-8")
    games = (root / "games.html").read_text(encoding="utf-8")
    assert "/api/game-portfolio/tracking/week" in routes
    assert "/api/game-portfolio/track" in routes
    assert "confirmed=confirmed" in routes
    assert "P4.7" in games
    assert "/api/game-portfolio/tracking/week" in games
    assert "/api/game-portfolio/track" in games
