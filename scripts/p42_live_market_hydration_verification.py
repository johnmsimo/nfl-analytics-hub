#!/usr/bin/env python3
"""Explicit live P4.2 hydration + persisted-board verification.

This script is intentionally NOT zero-credit. It must be launched only by the
protected P4.2 manual workflow after the operator selects the explicit live
confirmation token. Provider requests remain bounded by --max-targeted.
"""
from __future__ import annotations

import argparse
import json
from typing import Any

from database import db
import p42_live_market_hydration as p42


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--season", type=int, default=2026)
    parser.add_argument("--week", type=int, default=1)
    parser.add_argument("--type", dest="season_type", default="REG")
    parser.add_argument("--max-targeted", type=int, default=4)
    parser.add_argument("--min-priced-games", type=int, default=1)
    return parser.parse_args()


def _best_price_provenance(board: dict[str, Any]) -> bool:
    priced_markets = [
        market
        for row in board.get("rows", [])
        for market in (row.get("markets") or {}).values()
        if (market.get("pricing") or {}).get("quoteStatus") in {"fresh", "stale"}
    ]
    if not priced_markets:
        return False
    for market in priced_markets:
        best = (market.get("pricing") or {}).get("bestPrice")
        if not isinstance(best, dict):
            return False
        if not best.get("book") or best.get("price") is None or not best.get("quoteAt"):
            return False
    return True


def main() -> int:
    args = _parse_args()
    from app import app

    with app.app_context():
        hydration = p42.hydrate_week(
            args.season,
            args.week,
            args.season_type,
            allow_provider_spend=True,
            max_targeted_requests=args.max_targeted,
        )
        board = p42.build_cached_week_board(args.season, args.week, args.season_type)
        audit = p42.verify_board(board)
        db.session.rollback()

    gates = {
        "hydration_completed": hydration.get("ok") is True
        and hydration.get("state") == "hydrated",
        "provider_spend_explicit": hydration.get("providerSpendAllowed") is True,
        "provider_request_budget_respected": int(hydration.get("targetedRequests") or 0)
        <= max(0, min(p42.MAX_TARGETED_REQUESTS, args.max_targeted)),
        "complete_model_decision_board": int(board.get("gameCount") or 0) == 16
        and int(board.get("decisionCount") or 0) == 16,
        "real_market_hydrated": int(board.get("pricedGameCount") or 0)
        >= max(1, args.min_priced_games),
        "fresh_real_market_hydrated": int(board.get("freshPricedGameCount") or 0) >= 1,
        "real_market_coverage_present": bool(board.get("marketCoverage")),
        "best_price_provenance_present": _best_price_provenance(board),
        "persisted_cache_board_verified": audit.get("ok") is True,
        "product_reads_remain_cache_only": (board.get("safety") or {}).get(
            "cacheOnlyProductReads"
        )
        is True,
        "production_actionability_integrity": (
            audit.get("pricingAudit") or {}
        ).get("ok")
        is True,
    }
    blockers = [name for name, passed in gates.items() if not passed]
    payload = {
        "ok": not blockers,
        "phase": "P4.2",
        "mode": "explicit-live-hydration-then-cache-only-board",
        "blockingFailures": blockers,
        "gates": gates,
        "hydration": {
            "state": hydration.get("state"),
            "providerRequests": hydration.get("providerRequests"),
            "bulkEventCount": hydration.get("bulkEventCount"),
            "catalogEventCount": hydration.get("catalogEventCount"),
            "matchedGameCount": hydration.get("matchedGameCount"),
            "marketReadyGameCount": hydration.get("marketReadyGameCount"),
            "missingGameCount": hydration.get("missingGameCount"),
            "targetedRequests": hydration.get("targetedRequests"),
            "targetedWithMarkets": hydration.get("targetedWithMarkets"),
            "maxTargetedRequests": hydration.get("maxTargetedRequests"),
        },
        "board": {
            "gameCount": board.get("gameCount"),
            "decisionCount": board.get("decisionCount"),
            "pricedGameCount": board.get("pricedGameCount"),
            "freshPricedGameCount": board.get("freshPricedGameCount"),
            "actionableGameCount": board.get("actionableGameCount"),
            "marketCoverage": board.get("marketCoverage"),
            "freshMarketCoverage": board.get("freshMarketCoverage"),
            "actionableMarkets": board.get("actionableMarkets"),
            "hydrationAgeSeconds": board.get("hydrationAgeSeconds"),
        },
    }
    print(json.dumps(payload, sort_keys=True))
    return 0 if not blockers else 1


if __name__ == "__main__":
    raise SystemExit(main())
