#!/usr/bin/env python3
"""P5.7 zero-credit, zero-write market calibration console verification."""
from __future__ import annotations

import json
from pathlib import Path

from database import db
import p44_game_decision_ledger as p44
import p54_game_market_calibration as p54
import p56_game_market_calibration_control_plane as p56

HTML_PATH = Path("/app/model_operations.html")


def main() -> int:
    from app import app

    with app.app_context():
        receipts_before = p44.list_receipts(limit=2000)
        events_before = p54.list_events(limit=200)
        control = p56.build_production_control_plane()
        receipts_after = p44.list_receipts(limit=2000)
        events_after = p54.list_events(limit=200)
        db.session.rollback()

    html = HTML_PATH.read_text(encoding="utf-8")
    safety = control.get("safetyContract") or {}
    markets = control.get("markets") or {}
    valid_market_states = {
        "rollback-review",
        "champion-collecting",
        "champion-healthy",
        "champion-monitor",
        "promotion-review",
        "challenger-collecting",
        "baseline-monitor",
    }
    gates = {
        "console_file_present": HTML_PATH.exists(),
        "p57_console_present": "P5.7 · Spread &amp; Total Calibration Operations Console" in html,
        "canonical_p56_control_plane_used": "/api/game-market-calibration/control-plane" in html,
        "market_mutation_endpoints_not_hard_coded": "/api/game-market-calibration/promote" not in html
        and "/api/game-market-calibration/rollback" not in html,
        "owner_role_required_client_side": "role==='owner'" in html,
        "promotion_ready_required": "c.promoteReady===true" in html,
        "rollback_ready_required": "c.rollbackReady===true" in html,
        "exact_confirmation_required": "input.value!==command.confirmation" in html,
        "second_human_confirmation_required": "if(!confirm(text))return" in html,
        "spread_promotion_disabled_by_default": 'id="market-spread-promote-btn" type="button" disabled' in html,
        "spread_rollback_disabled_by_default": 'id="market-spread-rollback-btn" type="button" disabled' in html,
        "total_promotion_disabled_by_default": 'id="market-total-promote-btn" type="button" disabled' in html,
        "total_rollback_disabled_by_default": 'id="market-total-rollback-btn" type="button" disabled' in html,
        "mutations_bound_only_to_click_handlers": "runMarketCalibrationMutation(market,'promotion')" in html
        and "runMarketCalibrationMutation(market,'rollback')" in html,
        "mutation_payload_is_market_scoped": "{market,candidateId:candidate,confirmation:command.confirmation}" in html
        and "{market,confirmation:command.confirmation}" in html,
        "p55_rollback_recommendation_required": "rollback is not recommended by P5.5" in html,
        "market_isolation_visible": "Market-isolated governance" in html
        and "Spread/total governance remains isolated." in html,
        "production_state_valid": control.get("state") in {
            "rollback-review",
            "promotion-review",
            "collecting",
            "champions-healthy",
            "champions-monitor",
            "baseline-monitor",
        },
        "spread_state_valid": (markets.get("spread") or {}).get("state") in valid_market_states,
        "total_state_valid": (markets.get("total") or {}).get("state") in valid_market_states,
        "production_control_plane_read_only": safety.get("readOnly") is True,
        "production_market_isolated": safety.get("marketIsolated") is True,
        "production_zero_credit": safety.get("providerRequests") == 0,
        "automatic_promotion_disabled": safety.get("automaticPromotion") is False,
        "automatic_rollback_disabled": safety.get("automaticRollback") is False,
        "owner_confirmation_required_server_contract": safety.get("ownerConfirmationRequired") is True,
        "selected_side_changes_disabled": safety.get("changesSelectedSide") is False,
        "game_receipts_unchanged": receipts_before == receipts_after,
        "market_promotion_history_unchanged": events_before == events_after,
    }
    report = {
        "ok": all(gates.values()),
        "phase": "P5.7",
        "mode": "zero-credit-zero-write-market-calibration-operations-console-verification",
        "gates": gates,
        "blockingFailures": [key for key, passed in gates.items() if not passed],
        "production": {
            "state": control.get("state"),
            "recommendedAction": control.get("recommendedAction"),
            "promotionReviewMarkets": control.get("promotionReviewMarkets"),
            "rollbackReviewMarkets": control.get("rollbackReviewMarkets"),
            "activeChampionMarkets": control.get("activeChampionMarkets"),
            "spreadState": (markets.get("spread") or {}).get("state"),
            "spreadPromoteReady": (markets.get("spread") or {}).get("promoteReady"),
            "spreadRollbackReady": (markets.get("spread") or {}).get("rollbackReady"),
            "totalState": (markets.get("total") or {}).get("state"),
            "totalPromoteReady": (markets.get("total") or {}).get("promoteReady"),
            "totalRollbackReady": (markets.get("total") or {}).get("rollbackReady"),
            "receiptCount": len(receipts_before),
            "promotionEventCount": len(events_before),
        },
    }
    print(json.dumps(report, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
