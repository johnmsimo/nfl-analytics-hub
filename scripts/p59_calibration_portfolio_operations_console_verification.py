#!/usr/bin/env python3
"""P5.9 zero-credit, zero-write calibration portfolio console verification."""
from __future__ import annotations

import json
from pathlib import Path

from database import db
import p44_game_decision_ledger as p44
import p50_game_calibration_promotion as p50
import p54_game_market_calibration as p54
import p58_calibration_portfolio_control_plane as p58

PAGE = "static/p59_calibration_portfolio_operations.html"
VALID_STATES = {
    "rollback-review",
    "promotion-review",
    "collecting",
    "champions-healthy",
    "champions-monitor",
    "degraded-monitor",
    "baseline-monitor",
}


def main() -> int:
    from app import app

    page_path = Path(app.root_path) / PAGE
    page = page_path.read_text(encoding="utf-8")

    with app.app_context():
        receipts_before = p44.list_receipts(limit=2000)
        moneyline_events_before = p50.list_events(limit=100)
        market_events_before = p54.list_events(limit=200)
        portfolio = p58.build_production_portfolio()

        client = app.test_client()
        page_response = client.get(f"/{PAGE}")
        csp = page_response.headers.get("Content-Security-Policy", "")

        receipts_after = p44.list_receipts(limit=2000)
        moneyline_events_after = p50.list_events(limit=100)
        market_events_after = p54.list_events(limit=200)
        db.session.rollback()

    safety = portfolio.get("safetyContract") or {}
    markets = portfolio.get("markets") or {}
    gates = {
        "portfolio_state_valid": portfolio.get("state") in VALID_STATES,
        "three_market_portfolio_present": set(markets) == {"moneyline", "spread", "total"},
        "production_zero_credit": safety.get("providerRequests") == 0,
        "portfolio_read_only": safety.get("readOnly") is True,
        "automatic_promotion_disabled": safety.get("automaticPromotion") is False,
        "automatic_rollback_disabled": safety.get("automaticRollback") is False,
        "owner_confirmation_required": safety.get("ownerConfirmationRequired") is True,
        "moneyline_delegation_preserved": safety.get("delegatesMoneylineGatesToP52") is True,
        "spread_total_delegation_preserved": safety.get("delegatesSpreadTotalGatesToP56") is True,
        "console_page_served": page_response.status_code == 200,
        "strict_script_csp_active": "script-src" in csp and "'unsafe-inline'" not in csp.split("script-src", 1)[1].split(";", 1)[0],
        "console_loads_only_read_side_on_boot": "await loadPortfolio();" in page
        and "No promotion or rollback runs on page load or refresh." in page,
        "console_uses_portfolio_endpoint": "/api/game-calibration/portfolio-control-plane" in page,
        "mutation_endpoints_not_hard_coded": all(
            value not in page
            for value in (
                "/api/game-calibration/promote",
                "/api/game-calibration/rollback",
                "/api/game-market-calibration/promote",
                "/api/game-market-calibration/rollback",
            )
        )
        and "command.endpoint" in page,
        "buttons_disabled_by_default": 'id="${market}-promote-btn" type="button" disabled' in page
        and 'id="${market}-rollback-btn" type="button" disabled' in page,
        "exact_confirmation_and_second_review_required": "input.value!==command.confirmation" in page
        and "if(!confirm(prompt))return" in page,
        "game_receipts_unchanged": receipts_before == receipts_after,
        "moneyline_promotion_history_unchanged": moneyline_events_before == moneyline_events_after,
        "market_promotion_history_unchanged": market_events_before == market_events_after,
    }
    report = {
        "ok": all(gates.values()),
        "phase": "P5.9",
        "mode": "zero-credit-zero-write-calibration-portfolio-console-verification",
        "gates": gates,
        "blockingFailures": [key for key, passed in gates.items() if not passed],
        "production": {
            "state": portfolio.get("state"),
            "recommendedAction": portfolio.get("recommendedAction"),
            "promotionReviewMarkets": portfolio.get("promotionReviewMarkets"),
            "rollbackReviewMarkets": portfolio.get("rollbackReviewMarkets"),
            "activeChampionMarkets": portfolio.get("activeChampionMarkets"),
            "receiptCount": len(receipts_before),
            "moneylinePromotionEventCount": len(moneyline_events_before),
            "marketPromotionEventCount": len(market_events_before),
            "consolePath": f"/{PAGE}",
        },
    }
    print(json.dumps(report, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
