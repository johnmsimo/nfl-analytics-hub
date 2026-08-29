"""P5.8 unified all-market calibration portfolio control plane.

P5.2 owns canonical moneyline calibration governance. P5.6 owns canonical
spread/total calibration governance. P5.8 combines those already-governed
signals into one read-only portfolio decision across moneyline, spread, and
total without weakening or duplicating any lower-layer gate.

The portfolio never promotes or rolls back any champion. Mutation authority
remains exclusively in the existing owner-confirmed P5.0/P5.4 endpoints.
"""
from __future__ import annotations

from typing import Any

import p52_game_calibration_control_plane as p52
import p56_game_market_calibration_control_plane as p56

MODEL_NAME = "p5.8-calibration-portfolio-control-plane"
MODEL_VERSION = "p58-calibration-portfolio-v1"
MARKETS = ("moneyline", "spread", "total")


def _moneyline_view(control: dict[str, Any]) -> dict[str, Any]:
    commands = control.get("commands") if isinstance(control.get("commands"), dict) else {}
    evidence = control.get("evidence") if isinstance(control.get("evidence"), dict) else {}
    return {
        "available": control.get("available") is not False,
        "market": "moneyline",
        "state": control.get("state"),
        "recommendedAction": control.get("recommendedAction"),
        "message": control.get("message"),
        "candidateId": control.get("candidateId"),
        "championCandidateId": control.get("championCandidateId"),
        "championApplied": control.get("championApplied") is True,
        "promoteReady": control.get("promoteReady") is True,
        "rollbackReady": control.get("rollbackReady") is True,
        "blockers": list(control.get("blockers") or []),
        "evidence": {
            "challengerState": evidence.get("challengerState"),
            "challengerGradedSamples": evidence.get("challengerGradedSamples", 0),
            "promotionEligible": evidence.get("p50Eligible") is True,
            "guardState": evidence.get("guardState"),
            "guardGradedSamples": evidence.get("guardGradedSamples", 0),
            "rollbackRecommended": evidence.get("rollbackRecommended") is True,
        },
        "commands": commands,
        "governanceSource": "P5.2",
    }


def _market_view(market: str, control: dict[str, Any]) -> dict[str, Any]:
    evidence = control.get("evidence") if isinstance(control.get("evidence"), dict) else {}
    commands = control.get("commands") if isinstance(control.get("commands"), dict) else {}
    return {
        "available": control.get("available") is not False,
        "market": market,
        "state": control.get("state"),
        "recommendedAction": control.get("recommendedAction"),
        "message": control.get("message"),
        "candidateId": control.get("candidateId"),
        "championCandidateId": control.get("championCandidateId"),
        "championApplied": control.get("championApplied") is True,
        "promoteReady": control.get("promoteReady") is True,
        "rollbackReady": control.get("rollbackReady") is True,
        "blockers": list(control.get("blockers") or []),
        "evidence": {
            "challengerState": evidence.get("challengerState"),
            "challengerGradedSamples": evidence.get("challengerGradedSamples", 0),
            "promotionEligible": evidence.get("promotionEligible") is True,
            "guardState": evidence.get("guardState"),
            "guardGradedSamples": evidence.get("guardGradedSamples", 0),
            "rollbackRecommended": evidence.get("rollbackRecommended") is True,
        },
        "commands": commands,
        "governanceSource": "P5.6",
    }


def build_portfolio(
    moneyline_control: dict[str, Any],
    market_control: dict[str, Any],
) -> dict[str, Any]:
    """Return one canonical operating decision across all game markets."""
    market_controls = (
        market_control.get("markets") if isinstance(market_control.get("markets"), dict) else {}
    )
    markets = {
        "moneyline": _moneyline_view(moneyline_control),
        "spread": _market_view("spread", market_controls.get("spread") or {}),
        "total": _market_view("total", market_controls.get("total") or {}),
    }

    rollback_review = [
        market for market, row in markets.items() if row.get("rollbackReady") is True
    ]
    promotion_review = [
        market for market, row in markets.items() if row.get("promoteReady") is True
    ]
    active_champions = [
        market for market, row in markets.items() if row.get("championApplied") is True
    ]
    collecting = [
        market
        for market, row in markets.items()
        if row.get("state") in {"challenger-collecting", "champion-collecting", "collecting"}
    ]
    healthy = [
        market
        for market, row in markets.items()
        if row.get("state") in {"champion-healthy", "champions-healthy", "healthy"}
    ]
    unavailable = [
        market for market, row in markets.items() if row.get("available") is False
    ]

    # Safety priority is intentionally fail-closed: rollback review outranks all
    # promotion opportunities, which outrank passive collection/monitoring.
    if rollback_review:
        state = "rollback-review"
        action = "REVIEW_CALIBRATION_ROLLBACKS"
        message = "One or more promoted calibration champions require owner rollback review."
    elif promotion_review:
        state = "promotion-review"
        action = "REVIEW_CALIBRATION_PROMOTIONS"
        message = "One or more calibration challengers clear their existing promotion gates."
    elif collecting:
        state = "collecting"
        action = "COLLECT_MORE_CALIBRATION_RESULTS"
        message = "At least one calibration path is still collecting protected evidence."
    elif healthy and not unavailable:
        state = "champions-healthy"
        action = "KEEP_HEALTHY_CALIBRATION_CHAMPIONS"
        message = "All active promoted calibration champions remain inside their guardrails."
    elif active_champions:
        state = "champions-monitor"
        action = "MONITOR_CALIBRATION_CHAMPIONS"
        message = "Promoted calibration champions are active and remain under governance monitoring."
    elif unavailable:
        state = "degraded-monitor"
        action = "REVIEW_CALIBRATION_GOVERNANCE_AVAILABILITY"
        message = "One or more calibration governance sources are unavailable; mutation readiness remains fail-closed."
    else:
        state = "baseline-monitor"
        action = "KEEP_CALIBRATION_BASELINES"
        message = "Moneyline, spread, and total remain on baseline calibration with no mutation required."

    commands = {
        market: markets[market].get("commands") or {}
        for market in MARKETS
    }
    return {
        "available": not unavailable,
        "model": MODEL_NAME,
        "modelVersion": MODEL_VERSION,
        "state": state,
        "recommendedAction": action,
        "message": message,
        "markets": markets,
        "promotionReviewMarkets": promotion_review,
        "rollbackReviewMarkets": rollback_review,
        "activeChampionMarkets": active_champions,
        "collectingMarkets": collecting,
        "healthyChampionMarkets": healthy,
        "unavailableMarkets": unavailable,
        "commands": commands,
        "priorityContract": [
            "rollback-review",
            "promotion-review",
            "collecting",
            "champions-healthy",
            "champions-monitor",
            "degraded-monitor",
            "baseline-monitor",
        ],
        "safetyContract": {
            "readOnly": True,
            "providerRequests": 0,
            "writesMoneylinePromotionRegistry": False,
            "writesMarketPromotionRegistry": False,
            "automaticPromotion": False,
            "automaticRollback": False,
            "ownerConfirmationRequired": True,
            "delegatesMoneylineGatesToP52": True,
            "delegatesSpreadTotalGatesToP56": True,
            "changesModelProbabilities": False,
            "changesSelectedSide": False,
            "changesActionabilityThresholds": False,
            "changesBankrollPolicy": False,
            "placesBets": False,
        },
    }


def build_production_portfolio() -> dict[str, Any]:
    """Read P5.2/P5.6 production governance and return the P5.8 portfolio."""
    moneyline_control = p52.build_production_control_plane()
    market_control = p56.build_production_control_plane()
    return build_portfolio(moneyline_control, market_control)
