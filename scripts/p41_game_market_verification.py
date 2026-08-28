#!/usr/bin/env python3
"""Sanitized, zero-credit P4.1 production verification."""
from __future__ import annotations

import json
import time
from typing import Any

from database import db
import nfl_data
import p41_game_market_pricing as p41


def _synthetic_decision() -> dict[str, Any]:
    return {
        "gameId": "synthetic",
        "season": 2026,
        "seasonType": "REG",
        "week": 1,
        "homeTeam": "HOME",
        "awayTeam": "AWAY",
        "model": "p4.0-game-intelligence",
        "modelVersion": "p40-transparent-v1",
        "modelHomeMargin": 10.0,
        "homeWinProbability": 0.75,
        "awayWinProbability": 0.25,
        "confidenceScore": 84.0,
        "confidenceGrade": "A",
        "decisionGrade": "Strong Play",
        "selectedSide": "home",
        "selectedTeam": "HOME",
        "selectedProbability": 0.75,
        "evidence": {
            "home": {"basic": {"ppg": 27.0, "papg": 20.0}},
            "away": {"basic": {"ppg": 20.0, "papg": 27.0}},
        },
    }


def _synthetic_event() -> dict[str, Any]:
    books = []
    for key, home_ml, away_ml, spread_home, spread_away, over, under in (
        ("book-a", 110, -130, -105, -115, -105, -115),
        ("book-b", 105, -125, -110, -110, -110, -110),
    ):
        books.append(
            {
                "key": key,
                "title": key,
                "bookmakers": [],
                "markets": [
                    {
                        "key": "h2h",
                        "outcomes": [
                            {"name": "Home Team", "price": home_ml},
                            {"name": "Away Team", "price": away_ml},
                        ],
                    },
                    {
                        "key": "spreads",
                        "outcomes": [
                            {"name": "Home Team", "price": spread_home, "point": -3.5},
                            {"name": "Away Team", "price": spread_away, "point": 3.5},
                        ],
                    },
                    {
                        "key": "totals",
                        "outcomes": [
                            {"name": "Over", "price": over, "point": 40.5},
                            {"name": "Under", "price": under, "point": 40.5},
                        ],
                    },
                ],
            }
        )
    return {
        "id": "synthetic-event",
        "home_team": "Home Team",
        "away_team": "Away Team",
        "commence_time": "2026-09-10T00:00:00Z",
        "bookmakers": books,
    }


def main() -> int:
    from app import app

    season = nfl_data.default_season()
    with app.app_context():
        report = p41.build_week_market_report(
            season,
            1,
            "REG",
            pricing_mode="cache",
        )
        db.session.rollback()

    audit = p41.verify_actionability(report.get("rows") or [])
    fresh = p41.price_game_decision(
        _synthetic_decision(),
        _synthetic_event(),
        fetched_at=time.time(),
    )
    stale = p41.price_game_decision(
        _synthetic_decision(),
        _synthetic_event(),
        fetched_at=time.time() - 3600,
    )
    synthetic_markets = fresh.get("markets") or {}
    gates = {
        "week_one_model_board_available": report.get("available") is True,
        "complete_week_one_decision_coverage": int(report.get("gameCount") or 0) == 16
        and int(report.get("decisionCount") or 0) == 16,
        "cache_only_verification": report.get("pricingMode") == "cache",
        "production_actionability_integrity": audit.get("ok") is True,
        "fresh_quote_required": bool((report.get("safety") or {}).get("freshQuoteRequired")),
        "paired_fair_book_required": bool((report.get("safety") or {}).get("pairedFairBookRequired")),
        "explicit_live_refresh_only": bool((report.get("safety") or {}).get("liveRefreshRequiresExplicitMode")),
        "synthetic_moneyline_actionable": bool((synthetic_markets.get("moneyline") or {}).get("actionable")),
        "synthetic_spread_actionable": bool((synthetic_markets.get("spread") or {}).get("actionable")),
        "synthetic_total_actionable": bool((synthetic_markets.get("total") or {}).get("actionable")),
        "synthetic_stale_quotes_fail_closed": stale.get("actionable") is False
        and not stale.get("actionableMarkets"),
        "synthetic_best_price_has_provenance": all(
            ((market.get("pricing") or {}).get("bestPrice") or {}).get("quoteAt") is not None
            and ((market.get("pricing") or {}).get("bestPrice") or {}).get("quoteAgeSeconds") is not None
            for market in synthetic_markets.values()
        ),
    }
    blockers = [name for name, passed in gates.items() if not passed]
    payload = {
        "ok": not blockers,
        "phase": "P4.1",
        "mode": "read-only-cache-only",
        "blockingFailures": blockers,
        "gates": gates,
        "gameMarkets": {
            "gameCount": report.get("gameCount"),
            "decisionCount": report.get("decisionCount"),
            "pricedGameCount": report.get("pricedGameCount"),
            "actionableGameCount": report.get("actionableGameCount"),
            "marketCoverage": report.get("marketCoverage"),
            "actionableMarkets": report.get("actionableMarkets"),
            "gameSnapshotAgeSeconds": report.get("gameSnapshotAgeSeconds"),
            "audit": audit,
        },
    }
    print(json.dumps(payload, sort_keys=True))
    return 0 if not blockers else 1


if __name__ == "__main__":
    raise SystemExit(main())
