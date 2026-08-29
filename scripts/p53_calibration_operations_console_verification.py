#!/usr/bin/env python3
"""P5.3 zero-credit, zero-write calibration operations console verification."""
from __future__ import annotations

import json
from pathlib import Path

from database import db
import p44_game_decision_ledger as p44
import p50_game_calibration_promotion as p50
import p52_game_calibration_control_plane as p52

HTML_PATH = Path("/app/model_operations.html")


def main() -> int:
    from app import app

    with app.app_context():
        receipts_before = p44.list_receipts(limit=2000)
        events_before = p50.list_events(limit=100)
        control = p52.build_production_control_plane()
        receipts_after = p44.list_receipts(limit=2000)
        events_after = p50.list_events(limit=100)
        db.session.rollback()

    html = HTML_PATH.read_text(encoding="utf-8")
    safety = control.get("safetyContract") or {}
    gates = {
        "console_file_present": HTML_PATH.exists(),
        "p53_console_present": "P5.3 · Game Calibration Operations Console" in html,
        "canonical_control_plane_used": "/api/game-calibration/control-plane" in html,
        "owner_role_required_client_side": "role==='owner'" in html,
        "promotion_ready_required": "promoteReady===true" in html,
        "rollback_ready_required": "rollbackReady===true" in html,
        "exact_confirmation_required": "input.value!==command.confirmation" in html,
        "second_human_confirmation_required": "if(!confirm(text))return" in html,
        "promotion_button_disabled_by_default": 'id="promote-btn" type="button" disabled' in html,
        "rollback_button_disabled_by_default": 'id="rollback-btn" type="button" disabled' in html,
        "mutations_bound_to_click_handlers": "$('#promote-btn').onclick=()=>runCalibrationMutation('promotion');" in html
        and "$('#rollback-btn').onclick=()=>runCalibrationMutation('rollback');" in html,
        "production_state_valid": control.get("state") in {
            "rollback-review",
            "champion-collecting",
            "champion-healthy",
            "champion-monitor",
            "promotion-review",
            "challenger-collecting",
            "baseline-monitor",
        },
        "production_control_plane_read_only": safety.get("readOnly") is True,
        "production_zero_credit": safety.get("providerRequests") == 0,
        "automatic_promotion_disabled": safety.get("automaticPromotion") is False,
        "automatic_rollback_disabled": safety.get("automaticRollback") is False,
        "owner_confirmation_required_server_contract": safety.get("ownerConfirmationRequired") is True,
        "game_receipts_unchanged": receipts_before == receipts_after,
        "promotion_history_unchanged": events_before == events_after,
    }
    report = {
        "ok": all(gates.values()),
        "phase": "P5.3",
        "mode": "zero-credit-zero-write-calibration-operations-console-verification",
        "gates": gates,
        "blockingFailures": [key for key, passed in gates.items() if not passed],
        "production": {
            "state": control.get("state"),
            "recommendedAction": control.get("recommendedAction"),
            "promoteReady": control.get("promoteReady"),
            "rollbackReady": control.get("rollbackReady"),
            "candidateId": control.get("candidateId"),
            "championCandidateId": control.get("championCandidateId"),
            "receiptCount": len(receipts_before),
            "promotionEventCount": len(events_before),
        },
    }
    print(json.dumps(report, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
