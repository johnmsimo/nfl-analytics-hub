#!/usr/bin/env python3
"""Sanitized, zero-credit P4.5 production verification."""
from __future__ import annotations

import json

from database import db
from db_models import ScheduledJob
import p45_smart_market_refresh as p45


def _synthetic_delivery(quote_status: str) -> dict:
    return {
        "modelVersion": "p43-decision-board-v1",
        "summary": {},
        "safety": {"cacheOnly": True},
        "allMarkets": [
            {
                "gameId": "synthetic",
                "market": "moneyline",
                "pickLabel": "HOME ML",
                "selectedSide": "home",
                "selectedTeam": "HOME",
                "modelProbability": 0.66,
                "confidenceScore": 78.0,
                "decisionGrade": "Play",
                "quoteStatus": quote_status,
                "priceStatus": "positive_value",
                "fairMarketProbability": 0.56 if quote_status != "unpriced" else None,
                "referenceProbability": 0.56 if quote_status != "unpriced" else None,
                "edge": 0.10 if quote_status != "unpriced" else None,
                "evPct": 0.09 if quote_status != "unpriced" else None,
                "kellyPct": 0.03 if quote_status != "unpriced" else None,
                "freshBookCount": 0,
                "pairedFairBookCount": 0,
                "bestBook": "SyntheticBook" if quote_status != "unpriced" else None,
                "bestPrice": -105 if quote_status != "unpriced" else None,
                "actionable": False,
            }
        ],
    }


def main() -> int:
    from app import app

    with app.app_context():
        status = p45.refresh_status(2026)
        delivery = p45.build_week_opportunities(2026, 1, "REG", limit=20)
        audit = p45.verify_opportunity_contract(delivery)
        scheduler_row = db.session.scalar(
            db.select(ScheduledJob).where(ScheduledJob.key == "game-market-refresh")
        )
        db.session.rollback()

    stale = p45.enrich_delivery(_synthetic_delivery("stale"))
    unpriced = p45.enrich_delivery(_synthetic_delivery("unpriced"))
    stale_item = (stale.get("opportunities") or [{}])[0]
    unpriced_item = (unpriced.get("opportunities") or [{}])[0]
    summary = delivery.get("summary") or {}
    slate = status.get("slate") or {}

    gates = {
        "refresh_policy_enabled": status.get("enabled") is True,
        "next_slate_available": status.get("available") is True,
        "next_slate_is_2026_reg_week_one": slate.get("season") == 2026
        and slate.get("seasonType") == "REG"
        and slate.get("week") == 1,
        "status_check_is_zero_credit": status.get("providerSpend") is False,
        "scheduler_job_registered": scheduler_row is not None
        and bool(scheduler_row.enabled),
        "opportunity_contract_valid": audit.get("ok") is True,
        "opportunity_board_has_useful_model_pool": int(summary.get("visibleOpportunities") or 0) >= 4,
        "stale_positive_play_requires_refresh": stale_item.get("opportunityState") == "REFRESH"
        and stale_item.get("actionable") is False
        and "quote_not_fresh" in (stale_item.get("actionBlockers") or []),
        "unpriced_play_stays_model_only": unpriced_item.get("opportunityState") == "MODEL"
        and unpriced_item.get("actionable") is False,
        "p45_never_upgrades_actionability": (delivery.get("safety") or {}).get("p45NeverUpgradesActionability") is True,
        "product_reads_remain_provider_free": (delivery.get("safety") or {}).get("providerIoOnProductReads") is False,
    }
    blockers = [name for name, passed in gates.items() if not passed]
    payload = {
        "ok": not blockers,
        "phase": "P4.5",
        "mode": "zero-credit-smart-refresh-verification",
        "blockingFailures": blockers,
        "gates": gates,
        "refresh": {
            "state": status.get("state"),
            "enabled": status.get("enabled"),
            "due": status.get("due"),
            "cadenceSeconds": status.get("cadenceSeconds"),
            "slate": slate,
            "cache": status.get("cache"),
        },
        "opportunities": {
            "state": delivery.get("state"),
            "message": delivery.get("message"),
            "summary": summary,
            "publication": delivery.get("publication"),
        },
    }
    print(json.dumps(payload, sort_keys=True))
    return 0 if not blockers else 1


if __name__ == "__main__":
    raise SystemExit(main())
