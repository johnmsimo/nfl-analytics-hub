#!/usr/bin/env python3
"""Sanitized, zero-credit P4.3 user-facing game-board verification."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from database import db
import p43_game_decision_delivery as p43


def _synthetic_board() -> dict[str, Any]:
    pricing = {
        "quoteStatus": "fresh",
        "priceStatus": "positive_value",
        "fairMarketProbability": 0.55,
        "referenceProbability": 0.55,
        "edge": 0.10,
        "evPct": 0.16,
        "kellyPct": 0.08,
        "freshBookCount": 4,
        "pairedFairBookCount": 3,
        "bestPrice": {
            "book": "Verification Book",
            "price": 110,
            "quoteAt": "2099-09-10T00:00:00+00:00",
            "quoteAgeSeconds": 5.0,
            "expiresInSeconds": 115.0,
        },
    }
    return {
        "available": True,
        "modelVersion": "p42-hydration-v1",
        "season": 2026,
        "seasonType": "REG",
        "week": 1,
        "hydrationState": "available",
        "hydratedAt": "2099-09-10T00:00:00+00:00",
        "hydrationAgeSeconds": 5.0,
        "gameCount": 1,
        "pricedGameCount": 1,
        "freshPricedGameCount": 1,
        "actionableGameCount": 1,
        "rows": [
            {
                "gameId": "synthetic-game",
                "season": 2026,
                "seasonType": "REG",
                "week": 1,
                "kickoffAt": "2099-09-10T00:15:00Z",
                "homeTeam": "HOME",
                "awayTeam": "AWAY",
                "reasons": [{"factor": "team-strength edge"}],
                "risks": ["Normal game variance."],
                "markets": {
                    "moneyline": {
                        "market": "moneyline",
                        "line": None,
                        "selectedSide": "home",
                        "selectedTeam": "HOME",
                        "modelProbability": 0.65,
                        "confidenceScore": 80.0,
                        "decisionGrade": "Play",
                        "pricing": pricing,
                        "actionable": True,
                    }
                },
            }
        ],
    }


def _surface_markers() -> dict[str, bool]:
    root = Path("/app")
    dashboard = (root / "dashboard.html").read_text(encoding="utf-8")
    games = (root / "games.html").read_text(encoding="utf-8")
    return {
        "my_hub_p43_surface": "Best Game Bets · P4.3" in dashboard
        and "/api/game-market-board/week" in dashboard
        and "ACTIONABLE" in dashboard,
        "games_p43_surface": "P4.3 decision-first" in games
        and "/api/game-decision-board/week" in games
        and "ACTIONABLE" in games,
    }


def main() -> int:
    from app import app

    with app.app_context():
        production = p43.build_week_delivery(2026, 1, "REG", limit=12)
        production_audit = p43.verify_delivery(production)
        db.session.rollback()

    synthetic = p43.build_delivery_from_board(_synthetic_board())
    synthetic_audit = p43.verify_delivery(synthetic)
    surface_gates = _surface_markers()
    summary = production.get("summary") or {}
    all_markets = list(production.get("allMarkets") or [])
    production_picks = list(production.get("picks") or [])

    gates = {
        "complete_week_one_game_context": int(summary.get("games") or 0) == 16,
        "persisted_priced_board_present": int(summary.get("pricedGames") or 0) >= 1,
        "market_delivery_present": len(all_markets) >= 16,
        "production_delivery_integrity": production_audit.get("ok") is True,
        "production_cache_only_contract": (production.get("safety") or {}).get("cacheOnly") is True,
        "production_never_recalculates_actionability": (
            production.get("safety") or {}
        ).get("noActionabilityRecalculation") is True,
        "synthetic_actionable_pick_surfaces": len(synthetic.get("picks") or []) == 1,
        "synthetic_delivery_integrity": synthetic_audit.get("ok") is True,
        "synthetic_best_price_preserved": (synthetic.get("picks") or [{}])[0].get("bestBook")
        == "Verification Book",
        **surface_gates,
    }
    blockers = [name for name, passed in gates.items() if not passed]
    payload = {
        "ok": not blockers,
        "phase": "P4.3",
        "mode": "read-only-cache-only-ui-integration",
        "blockingFailures": blockers,
        "gates": gates,
        "decisionBoard": {
            "state": production.get("state"),
            "gameCount": summary.get("games"),
            "pricedGameCount": summary.get("pricedGames"),
            "freshPricedGameCount": summary.get("freshPricedGames"),
            "actionableGameCount": summary.get("actionableGames"),
            "actionableMarketCount": summary.get("actionableMarkets"),
            "watchlistMarketCount": summary.get("watchlistMarkets"),
            "deliveredPickCount": len(production_picks),
            "marketCount": len(all_markets),
            "hydrationState": production.get("hydrationState"),
            "hydrationAgeSeconds": production.get("hydrationAgeSeconds"),
        },
        "productionAudit": production_audit,
        "syntheticAudit": synthetic_audit,
    }
    print(json.dumps(payload, sort_keys=True))
    return 0 if not blockers else 1


if __name__ == "__main__":
    raise SystemExit(main())
