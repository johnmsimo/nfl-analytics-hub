#!/usr/bin/env python3
"""Sanitized, zero-credit, zero-write P4.7 production verification."""
from __future__ import annotations

import json

from database import db
import p45_smart_market_refresh as p45
import p46_game_portfolio as p46
import p47_portfolio_tracker as p47


def _synthetic_report() -> dict:
    row = {
        "gameId": "p47-synthetic-game",
        "season": 2026,
        "seasonType": "REG",
        "week": 1,
        "kickoffAt": "2026-09-13T17:00:00+00:00",
        "homeTeam": "Synthetic Home",
        "awayTeam": "Synthetic Away",
        "market": "moneyline",
        "marketLabel": "Moneyline",
        "pickLabel": "Synthetic Home ML",
        "selectedSide": "home",
        "selectedTeam": "Synthetic Home",
        "line": None,
        "modelProbability": 0.62,
        "confidenceScore": 82.0,
        "decisionGrade": "Strong Play",
        "quoteStatus": "fresh",
        "priceStatus": "positive_value",
        "fairMarketProbability": 0.54,
        "referenceProbability": 0.55,
        "edge": 0.08,
        "evPct": 0.10,
        "kellyPct": 0.05,
        "freshBookCount": 4,
        "pairedFairBookCount": 3,
        "bestBook": "SyntheticBook",
        "bestPrice": -105,
        "quoteAt": "2026-09-13T16:58:00+00:00",
        "quoteAgeSeconds": 10,
        "actionable": True,
        "opportunityState": "ACTIONABLE",
        "portfolioEligible": True,
        "requestedStakePct": 0.025,
        "requestedStakeDollars": 25.0,
        "recommendedStakePct": 0.025,
        "recommendedStakeDollars": 25.0,
        "recommendedStakeUnits": 2.5,
        "reasons": ["Synthetic verified edge"],
        "risks": ["Synthetic price movement risk"],
    }
    return {
        "available": True,
        "model": p46.MODEL_NAME,
        "modelVersion": p46.MODEL_VERSION,
        "season": 2026,
        "seasonType": "REG",
        "week": 1,
        "portfolio": [row],
        "summary": {"portfolioPicks": 1, "allocatedStakeDollars": 25.0},
        "safety": {"providerIo": False, "automaticBetPlacement": False},
    }


def main() -> int:
    from app import app

    with app.app_context():
        refresh = p45.refresh_status()
        slate = refresh.get("slate") or {}
        target_ok = (
            refresh.get("available") is True
            and slate.get("season") is not None
            and slate.get("week") is not None
            and slate.get("seasonType") is not None
        )
        if target_ok:
            season = int(slate["season"])
            week = int(slate["week"])
            season_type = str(slate["seasonType"]).upper()
            portfolio = p46.build_week_portfolio(season, week, season_type)
            portfolio_audit = p46.verify_portfolio(portfolio)
            tracking = p47.build_tracking_status_from_portfolio(portfolio)
        else:
            season = None
            week = None
            season_type = None
            portfolio = {}
            portfolio_audit = {"ok": False}
            tracking = {}
        db.session.rollback()

    synthetic = _synthetic_report()
    row = synthetic["portfolio"][0]
    displayed_key = p47.tracking_key(row)
    payload = p47.to_tracker_payload(row, synthetic)
    blocked = p47.confirm_portfolio_from_report(
        synthetic,
        confirmed=False,
        persist=False,
    )
    dry_run = p47.confirm_portfolio_from_report(
        synthetic,
        confirmed=True,
        selection_keys=[displayed_key],
        persist=False,
    )
    empty = p47.confirm_portfolio_from_report(
        synthetic,
        confirmed=True,
        selection_keys=[],
        persist=False,
    )
    changed = _synthetic_report()
    changed["portfolio"][0]["bestPrice"] = -110
    stale = p47.confirm_portfolio_from_report(
        changed,
        confirmed=True,
        selection_keys=[displayed_key],
        persist=False,
    )
    unknown = p47.confirm_portfolio_from_report(
        synthetic,
        confirmed=True,
        selection_keys=["not-current"],
        persist=False,
    )

    summary = tracking.get("summary") or {}
    tracked = int(summary.get("trackedPicks") or 0)
    untracked = int(summary.get("untrackedPicks") or 0)
    portfolio_picks = int(summary.get("portfolioPicks") or 0)
    valid_week = week is not None and (
        (season_type == "PRE" and week >= 0)
        or (season_type in {"REG", "POST"} and week >= 1)
    )
    gates = {
        "next_slate_available": target_ok,
        "next_slate_identity_valid": season is not None
        and season_type in {"PRE", "REG", "POST"}
        and valid_week,
        "production_portfolio_contract_valid": portfolio_audit.get("ok") is True,
        "tracking_matches_current_portfolio": tracking.get("season") == season
        and tracking.get("seasonType") == season_type
        and tracking.get("week") == week,
        "tracking_counts_reconcile": tracked + untracked == portfolio_picks,
        "production_tracking_is_read_only": (tracking.get("safety") or {}).get("trackerWrite") is False,
        "production_tracking_is_zero_credit": (tracking.get("safety") or {}).get("providerIo") is False,
        "production_never_places_bets": (tracking.get("safety") or {}).get("automaticBetPlacement") is False,
        "confirmation_binds_exact_allocation": (tracking.get("safety") or {}).get("confirmationBindsExactAllocation") is True,
        "explicit_confirmation_is_required": blocked.get("ok") is False
        and blocked.get("error") == "explicit_confirmation_required"
        and blocked.get("saved") == 0,
        "confirmed_dry_run_plans_without_writing": dry_run.get("ok") is True
        and dry_run.get("mode") == "dry-run"
        and dry_run.get("planned") == 1
        and dry_run.get("saved") == 0
        and (dry_run.get("safety") or {}).get("trackerWrite") is False,
        "empty_selection_stays_empty": empty.get("ok") is True
        and empty.get("planned") == 0
        and empty.get("saved") == 0,
        "changed_allocation_rejects_stale_confirmation": stale.get("ok") is False
        and stale.get("error") == "unknown_portfolio_selection",
        "unknown_selection_fails_closed": unknown.get("ok") is False
        and unknown.get("error") == "unknown_portfolio_selection",
        "moneyline_maps_to_tracker_h2h": payload.get("marketKey") == "h2h",
        "verified_price_and_stake_preserved": payload.get("price") == -105
        and payload.get("book") == "SyntheticBook"
        and payload.get("stakeDollars") == 25.0
        and payload.get("stakeUnits") == 2.5,
    }
    blockers = [name for name, passed in gates.items() if not passed]
    output = {
        "ok": not blockers,
        "phase": "P4.7",
        "mode": "zero-credit-zero-write-portfolio-tracker-verification",
        "blockingFailures": blockers,
        "gates": gates,
        "target": {"season": season, "seasonType": season_type, "week": week},
        "production": {
            "portfolioState": portfolio.get("state"),
            "portfolioPicks": portfolio_picks,
            "trackedPicks": tracked,
            "untrackedPicks": untracked,
            "trackingState": tracking.get("state"),
        },
    }
    print(json.dumps(output, sort_keys=True))
    return 0 if not blockers else 1


if __name__ == "__main__":
    raise SystemExit(main())
