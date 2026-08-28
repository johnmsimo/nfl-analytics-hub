#!/usr/bin/env python3
"""Sanitized, zero-credit P4.6 bankroll portfolio production verification."""
from __future__ import annotations

import json

from database import db
import p45_smart_market_refresh as p45
import p46_game_portfolio as p46


def _synthetic_item(game_id: str, market: str, *, state: str = "ACTIONABLE") -> dict:
    actionable = state == "ACTIONABLE"
    return {
        "gameId": game_id,
        "market": market,
        "selectedSide": "home",
        "selectedTeam": f"{game_id}-HOME",
        "pickLabel": f"{game_id}-HOME {market}",
        "opportunityState": state,
        "actionable": actionable,
        "quoteStatus": "fresh" if state in {"ACTIONABLE", "WATCH"} else "unpriced",
        "priceStatus": "positive_value" if actionable else "unpriced",
        "decisionGrade": "Strong Play" if market == "moneyline" else "Play",
        "confidenceScore": 82.0,
        "modelProbability": 0.66,
        "fairMarketProbability": 0.55 if actionable else None,
        "edge": 0.11 if actionable else None,
        "evPct": 0.12 if actionable else None,
        "kellyPct": 0.20 if actionable else None,
        "bestBook": "SyntheticBook" if actionable else None,
        "bestPrice": -105 if actionable else None,
    }


def _synthetic_board() -> dict:
    rows = [
        _synthetic_item("g1", "moneyline"),
        _synthetic_item("g1", "spread"),
        _synthetic_item("g2", "moneyline"),
        _synthetic_item("g3", "moneyline", state="MODEL"),
    ]
    return {
        "available": True,
        "modelVersion": "p45-refresh-v1",
        "season": 2026,
        "seasonType": "REG",
        "week": 1,
        "summary": {"visibleOpportunities": 4, "actionableOpportunities": 3},
        "allOpportunities": rows,
    }


def main() -> int:
    from app import app

    with app.app_context():
        refresh = p45.refresh_status(2026)
        slate = refresh.get("slate") or {}
        target_ok = (
            refresh.get("available") is True
            and slate.get("season") is not None
            and slate.get("week") is not None
            and slate.get("seasonType") is not None
        )
        if target_ok:
            target_season = int(slate["season"])
            target_week = int(slate["week"])
            target_type = str(slate["seasonType"]).upper()
            production = p46.build_week_portfolio(target_season, target_week, target_type)
            production_audit = p46.verify_portfolio(production)
        else:
            target_season = None
            target_week = None
            target_type = None
            production = {}
            production_audit = {"ok": False, "gates": {}}
        db.session.rollback()

    synthetic = p46.build_portfolio_from_opportunities(
        _synthetic_board(),
        settings={
            "bankroll": 1000.0,
            "kelly_fraction": 1.0,
            "max_bet_pct": 0.05,
            "unit_pct": 0.01,
        },
    )
    synthetic_audit = p46.verify_portfolio(synthetic)
    synthetic_portfolio = list(synthetic.get("portfolio") or [])
    synthetic_context = list(synthetic.get("context") or [])

    no_action = p46.build_portfolio_from_opportunities(
        {
            "available": True,
            "modelVersion": "p45-refresh-v1",
            "season": 2026,
            "seasonType": "REG",
            "week": 1,
            "summary": {"visibleOpportunities": 1, "actionableOpportunities": 0},
            "allOpportunities": [_synthetic_item("g4", "moneyline", state="MODEL")],
        },
        settings={
            "bankroll": 1000.0,
            "kelly_fraction": 0.25,
            "max_bet_pct": 0.05,
            "unit_pct": 0.01,
        },
    )

    valid_week = target_week is not None and (
        (target_type == "PRE" and target_week >= 0)
        or (target_type in {"REG", "POST"} and target_week >= 1)
    )
    gates = {
        "next_slate_available": target_ok,
        "next_slate_identity_valid": target_season == 2026
        and target_type in {"PRE", "REG", "POST"}
        and valid_week,
        "production_portfolio_matches_next_slate": production.get("season") == target_season
        and production.get("seasonType") == target_type
        and production.get("week") == target_week,
        "production_portfolio_contract_valid": production_audit.get("ok") is True,
        "production_reads_are_zero_credit": (production.get("safety") or {}).get("providerIo") is False,
        "production_never_auto_bets": (production.get("safety") or {}).get("automaticBetPlacement") is False,
        "synthetic_portfolio_allocates_actionable_value": len(synthetic_portfolio) >= 1
        and float((synthetic.get("summary") or {}).get("allocatedStakeDollars") or 0.0) > 0.0,
        "synthetic_portfolio_contract_valid": synthetic_audit.get("ok") is True,
        "synthetic_non_actionable_stays_zero_stake": all(
            float(row.get("recommendedStakeDollars") or 0.0) == 0.0
            for row in synthetic_context
        ),
        "no_actionable_market_means_no_stake": no_action.get("portfolio") == []
        and float((no_action.get("summary") or {}).get("allocatedStakeDollars") or 0.0) == 0.0,
        "exposure_caps_are_present": float((production.get("safety") or {}).get("perGameCapPct") or 0.0) > 0.0
        and float((production.get("safety") or {}).get("slateCapPct") or 0.0) > 0.0,
    }
    blockers = [name for name, passed in gates.items() if not passed]
    summary = production.get("summary") or {}
    payload = {
        "ok": not blockers,
        "phase": "P4.6",
        "mode": "zero-credit-bankroll-portfolio-verification",
        "blockingFailures": blockers,
        "gates": gates,
        "target": {
            "season": target_season,
            "seasonType": target_type,
            "week": target_week,
        },
        "portfolio": {
            "state": production.get("state"),
            "message": production.get("message"),
            "portfolioPicks": summary.get("portfolioPicks"),
            "eligibleActionableCandidates": summary.get("eligibleActionableCandidates"),
            "allocatedStakeDollars": summary.get("allocatedStakeDollars"),
            "allocatedStakePct": summary.get("allocatedStakePct"),
            "unitDollars": summary.get("unitDollars"),
            "safety": production.get("safety"),
        },
    }
    print(json.dumps(payload, sort_keys=True))
    return 0 if not blockers else 1


if __name__ == "__main__":
    raise SystemExit(main())
