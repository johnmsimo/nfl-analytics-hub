from __future__ import annotations

from pathlib import Path

import p43_game_decision_delivery as p43


def _pricing(
    *,
    quote_status: str = "fresh",
    price_status: str = "positive_value",
    edge: float = 0.08,
    ev: float = 0.12,
    book: str = "Book A",
    price: int = 110,
    fair: float = 0.56,
) -> dict:
    return {
        "quoteStatus": quote_status,
        "priceStatus": price_status,
        "fairMarketProbability": fair,
        "referenceProbability": fair,
        "edge": edge,
        "evPct": ev,
        "kellyPct": 0.05,
        "freshBookCount": 4 if quote_status == "fresh" else 0,
        "pairedFairBookCount": 3 if quote_status == "fresh" else 0,
        "bestPrice": {
            "book": book,
            "price": price,
            "quoteAt": "2026-09-10T00:00:00+00:00",
            "quoteAgeSeconds": 20.0,
            "expiresInSeconds": 100.0,
        },
    }


def _market(
    key: str,
    *,
    actionable: bool,
    grade: str,
    quote_status: str = "fresh",
    selected_side: str = "home",
    selected_team: str | None = "AAA",
    line: float | None = None,
    edge: float = 0.08,
    ev: float = 0.12,
) -> dict:
    return {
        "market": key,
        "line": line,
        "selectedSide": selected_side,
        "selectedTeam": selected_team,
        "modelProbability": 0.64,
        "confidenceScore": 78.0,
        "decisionGrade": grade,
        "pricing": _pricing(
            quote_status=quote_status,
            price_status="positive_value" if actionable else "thin_value",
            edge=edge,
            ev=ev,
        ),
        "actionable": actionable,
    }


def _board() -> dict:
    return {
        "available": True,
        "modelVersion": "p42-hydration-v1",
        "season": 2026,
        "seasonType": "REG",
        "week": 1,
        "hydrationState": "available",
        "hydratedAt": "2026-09-09T23:59:00+00:00",
        "hydrationAgeSeconds": 30.0,
        "gameCount": 2,
        "pricedGameCount": 2,
        "freshPricedGameCount": 2,
        "actionableGameCount": 1,
        "rows": [
            {
                "gameId": "g1",
                "season": 2026,
                "seasonType": "REG",
                "week": 1,
                "kickoffAt": "2026-09-10T00:00:00Z",
                "homeTeam": "AAA",
                "awayTeam": "BBB",
                "reasons": [{"factor": "team-strength edge"}],
                "risks": ["Early-season evidence."],
                "markets": {
                    "moneyline": _market(
                        "moneyline",
                        actionable=True,
                        grade="Strong Play",
                        selected_side="home",
                        selected_team="AAA",
                    ),
                    "spread": _market(
                        "spread",
                        actionable=False,
                        grade="Lean",
                        selected_side="home",
                        selected_team="AAA",
                        line=-2.5,
                        edge=0.02,
                        ev=0.01,
                    ),
                    "total": _market(
                        "total",
                        actionable=False,
                        grade="Play",
                        quote_status="stale",
                        selected_side="over",
                        selected_team=None,
                        line=44.5,
                    ),
                },
            },
            {
                "gameId": "g2",
                "season": 2026,
                "seasonType": "REG",
                "week": 1,
                "kickoffAt": "2026-09-11T00:00:00Z",
                "homeTeam": "CCC",
                "awayTeam": "DDD",
                "reasons": [{"factor": "home field"}],
                "risks": [],
                "markets": {
                    "moneyline": _market(
                        "moneyline",
                        actionable=False,
                        grade="Play",
                        selected_side="away",
                        selected_team="DDD",
                        edge=0.01,
                        ev=0.01,
                    )
                },
            },
        ],
    }


def test_delivery_surfaces_only_upstream_actionable_market_as_pick():
    delivery = p43.build_delivery_from_board(_board())
    assert delivery["state"] == "actionable"
    assert delivery["summary"]["actionableMarkets"] == 1
    assert len(delivery["picks"]) == 1
    pick = delivery["picks"][0]
    assert pick["gameId"] == "g1"
    assert pick["market"] == "moneyline"
    assert pick["pickLabel"] == "AAA ML"
    assert pick["bestBook"] == "Book A"
    assert pick["bestPrice"] == 110
    assert pick["fairMarketProbability"] == 0.56
    assert pick["edge"] == 0.08
    assert pick["evPct"] == 0.12
    assert pick["actionable"] is True


def test_fresh_lean_or_better_non_actionable_rows_become_watchlist_only():
    delivery = p43.build_delivery_from_board(_board())
    keys = {(row["gameId"], row["market"]) for row in delivery["watchlist"]}
    assert ("g1", "spread") in keys
    assert ("g2", "moneyline") in keys
    assert ("g1", "total") not in keys  # stale quotes never enter the fresh watchlist


def test_delivery_audit_proves_no_actionability_upgrade():
    delivery = p43.build_delivery_from_board(_board())
    audit = p43.verify_delivery(delivery)
    assert audit["ok"] is True
    assert all(audit["gates"].values())


def test_game_delivery_filters_one_game_without_repricing():
    delivery = p43.build_delivery_from_board(_board())
    game = p43.game_delivery(delivery, "g1")
    assert game["state"] == "actionable"
    assert len(game["picks"]) == 1
    assert {row["market"] for row in game["markets"]} == {"moneyline", "spread", "total"}


def test_build_week_delivery_uses_p42_cached_board_only(monkeypatch):
    calls: list[tuple[int, int, str]] = []

    def _cached(season: int, week: int, season_type: str):
        calls.append((season, week, season_type))
        return _board()

    monkeypatch.setattr(p43.p42, "build_cached_week_board", _cached)
    delivery = p43.build_week_delivery(2026, 1, "reg")
    assert calls == [(2026, 1, "REG")]
    assert delivery["safety"]["cacheOnly"] is True
    assert delivery["safety"]["noActionabilityRecalculation"] is True


def test_p43_user_surfaces_reference_game_decision_board_endpoint():
    root = Path(__file__).resolve().parents[1]
    dashboard = (root / "dashboard.html").read_text(encoding="utf-8")
    games = (root / "games.html").read_text(encoding="utf-8")
    assert "Best Game Bets · P4.3" in dashboard
    assert "/api/game-market-board/week" in dashboard
    assert "P4.3 decision-first" in games
    assert "/api/game-decision-board/week" in games
    assert "ACTIONABLE" in games
