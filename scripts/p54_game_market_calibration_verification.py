#!/usr/bin/env python3
"""P5.4 zero-credit/zero-write spread-total calibration verification."""
from __future__ import annotations

import json

from database import db
import p44_game_decision_ledger as p44
import p54_game_market_calibration as p54


def _receipt(idx: int, market: str) -> dict:
    probability = 0.80
    return {
        "receiptId": f"p54-{market}-{idx:04d}",
        "releasedAt": f"2026-09-{(idx // 24) + 1:02d}T{idx % 24:02d}:00:00+00:00",
        "grade": "win" if idx % 2 == 0 else "loss",
        "release": {
            "gameId": f"p54-{market}-game-{idx:04d}",
            "marketKey": market,
            "modelProbability": probability,
            "fairMarketProbability": 0.50,
        },
        "result": {"probability": probability},
    }


def _synthetic_report(market: str) -> dict:
    return p54.build_market_candidate_report(
        [_receipt(idx, market) for idx in range(80)],
        market,
    )


def main() -> int:
    from app import app

    with app.app_context():
        receipts_before = p44.list_receipts(limit=2000)
        events_before = p54.list_events(limit=200)
        production = p54.build_production_report()

        spread = _synthetic_report("spread")
        total = _synthetic_report("total")
        spread_dry_run = p54.promote_candidate(
            "spread",
            spread["candidate"]["candidateId"],
            confirmation=p54.PROMOTE_CONFIRMATION,
            actor="p54-production-verifier",
            persist=False,
            report=spread,
        )
        total_dry_run = p54.promote_candidate(
            "total",
            total["candidate"]["candidateId"],
            confirmation=p54.PROMOTE_CONFIRMATION,
            actor="p54-production-verifier",
            persist=False,
            report=total,
        )
        rejected_confirmation = p54.promote_candidate(
            "spread",
            spread["candidate"]["candidateId"],
            confirmation="NO",
            actor="p54-production-verifier",
            persist=False,
            report=spread,
        )
        calibrated = p54.apply_to_selected_probability(
            "spread",
            0.64,
            champion={
                "state": "promoted",
                "applied": True,
                "candidateId": "p54-sp-verification",
                "family": "logit-affine",
                "parameters": {"slope": 0.75, "intercept": 0.0},
                "approvedAt": "synthetic",
            },
        )

        receipts_after = p44.list_receipts(limit=2000)
        events_after = p54.list_events(limit=200)
        db.session.rollback()

    safety = production.get("safetyContract") or {}
    markets = production.get("markets") or {}
    champions = production.get("champions") or {}
    valid_states = {"collecting", "review", "rejected"}
    gates = {
        "production_available": production.get("available") is True,
        "spread_state_valid": (markets.get("spread") or {}).get("state") in valid_states,
        "total_state_valid": (markets.get("total") or {}).get("state") in valid_states,
        "spread_champion_state_valid": (champions.get("spread") or {}).get("state") in {"baseline", "promoted"},
        "total_champion_state_valid": (champions.get("total") or {}).get("state") in {"baseline", "promoted"},
        "production_zero_credit": safety.get("providerRequests") == 0,
        "market_isolated_training": safety.get("marketIsolatedTraining") is True,
        "automatic_promotion_disabled": safety.get("automaticPromotion") is False,
        "automatic_rollback_disabled": safety.get("automaticRollback") is False,
        "synthetic_spread_review_passes": spread.get("state") == "review" and (spread.get("promotionGate") or {}).get("eligible") is True,
        "synthetic_total_review_passes": total.get("state") == "review" and (total.get("promotionGate") or {}).get("eligible") is True,
        "spread_dry_run_promotion_passes": spread_dry_run.get("ok") is True and spread_dry_run.get("dryRun") is True,
        "total_dry_run_promotion_passes": total_dry_run.get("ok") is True and total_dry_run.get("dryRun") is True,
        "invalid_confirmation_rejected": rejected_confirmation.get("code") == "CONFIRMATION_REQUIRED",
        "selected_side_probability_remains_bounded": calibrated.get("applied") is True and 0.5 <= float(calibrated.get("probability")) <= 0.999,
        "game_receipts_unchanged": receipts_before == receipts_after,
        "market_promotion_history_unchanged": events_before == events_after,
    }
    report = {
        "ok": all(gates.values()),
        "phase": "P5.4",
        "mode": "zero-credit-zero-write-market-calibration-verification",
        "gates": gates,
        "blockingFailures": [key for key, passed in gates.items() if not passed],
        "production": {
            "state": production.get("state"),
            "spreadState": (markets.get("spread") or {}).get("state"),
            "spreadSamples": (markets.get("spread") or {}).get("gradedSamples"),
            "spreadChampion": (champions.get("spread") or {}).get("state"),
            "totalState": (markets.get("total") or {}).get("state"),
            "totalSamples": (markets.get("total") or {}).get("gradedSamples"),
            "totalChampion": (champions.get("total") or {}).get("state"),
            "receiptCount": len(receipts_before),
            "promotionEventCount": len(events_before),
        },
    }
    print(json.dumps(report, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
