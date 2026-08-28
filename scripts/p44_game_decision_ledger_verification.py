#!/usr/bin/env python3
"""P4.4 production verification: cache-only publication + ledger safety checks."""
from __future__ import annotations

import json

from database import db
import decision_ledger
import nfl_data
import p44_game_decision_ledger as p44


def _synthetic_pick(market: str, side: str, line=None) -> dict:
    return {
        "gameId": "synthetic-p44",
        "season": 2026,
        "seasonType": "REG",
        "week": 1,
        "kickoffAt": "2026-09-10T00:00:00Z",
        "homeTeam": "HOME",
        "awayTeam": "AWAY",
        "market": market,
        "marketLabel": market.title(),
        "pickLabel": f"{side} {market}",
        "selectedSide": side,
        "selectedTeam": "HOME" if side == "home" else ("AWAY" if side == "away" else None),
        "line": line,
        "modelProbability": 0.64,
        "confidenceScore": 78.0,
        "decisionGrade": "Play",
        "fairMarketProbability": 0.56,
        "referenceProbability": 0.56,
        "edge": 0.08,
        "evPct": 0.07,
        "kellyPct": 0.02,
        "bestBook": "SyntheticBook",
        "bestPrice": -110,
        "quoteAt": "2026-08-28T19:00:00+00:00",
        "quoteAgeSeconds": 10.0,
        "freshBookCount": 4,
        "pairedFairBookCount": 3,
        "quoteStatus": "fresh",
        "priceStatus": "positive_value",
        "actionable": True,
        "reasons": ["synthetic contract"],
        "risks": ["synthetic only"],
        "sourceModelVersion": "p42-hydration-v1",
    }


def main() -> int:
    from app import app

    season = nfl_data.default_season()
    week = 1
    season_type = "REG"
    with app.app_context():
        player_before = decision_ledger.ledger_status()
        status_before = p44.ledger_status()
        first = p44.publish_week_delivery(season, week, season_type)
        second = p44.publish_week_delivery(season, week, season_type)
        status_after = p44.ledger_status()
        player_after = decision_ledger.ledger_status()
        performance = p44.performance_summary()
        db.session.rollback()

    first_pub = first.get("publication") or {}
    second_pub = second.get("publication") or {}
    candidates = int(first_pub.get("candidates") or 0)

    moneyline = p44.grade_market_release(
        p44.build_receipt(_synthetic_pick("moneyline", "home"))["release"],
        home_score=27,
        away_score=20,
    )
    spread = p44.grade_market_release(
        p44.build_receipt(_synthetic_pick("spread", "away", 3.5))["release"],
        home_score=24,
        away_score=21,
    )
    total = p44.grade_market_release(
        p44.build_receipt(_synthetic_pick("total", "over", 44.5))["release"],
        home_score=27,
        away_score=20,
    )
    push = p44.grade_market_release(
        p44.build_receipt(_synthetic_pick("spread", "away", 3.0))["release"],
        home_score=24,
        away_score=21,
    )

    gates = {
        "ledger_available": status_after.get("available") is True,
        "isolated_from_player_prop_ledger": status_after.get("isolatedFromPlayerPropLedger") is True,
        "player_prop_ledger_unchanged": player_before.get("receipts") == player_after.get("receipts"),
        "week_delivery_available": first.get("available") is True,
        "publication_matches_upstream_picks": candidates == len(first.get("picks") or []),
        "first_publication_fully_accounted": int(first_pub.get("inserted") or 0) + int(first_pub.get("existing") or 0) == candidates and int(first_pub.get("failed") or 0) == 0,
        "repeat_publication_is_idempotent": int(second_pub.get("inserted") or 0) == 0 and int(second_pub.get("existing") or 0) == int(second_pub.get("candidates") or 0),
        "receipt_count_never_decreases": int(status_after.get("receipts") or 0) >= int(status_before.get("receipts") or 0),
        "performance_available": performance.get("available") is True,
        "synthetic_moneyline_grading": moneyline is not None and moneyline[0] == "win",
        "synthetic_spread_grading": spread is not None and spread[0] == "win",
        "synthetic_total_grading": total is not None and total[0] == "win",
        "synthetic_push_grading": push is not None and push[0] == "push",
    }
    blockers = [name for name, passed in gates.items() if not passed]
    payload = {
        "ok": not blockers,
        "phase": "P4.4",
        "mode": "cache-only-first-publication",
        "blockingFailures": blockers,
        "gates": gates,
        "gameLedger": {
            "before": status_before,
            "after": status_after,
            "publication": first_pub,
            "repeatPublication": second_pub,
            "performance": performance,
            "deliveryState": first.get("state"),
            "actionablePicks": len(first.get("picks") or []),
        },
    }
    print(json.dumps(payload, sort_keys=True))
    return 0 if not blockers else 1


if __name__ == "__main__":
    raise SystemExit(main())
