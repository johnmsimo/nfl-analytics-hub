"""P5.6 unified spread/total calibration control plane.

P5.4 owns market-isolated challengers plus explicit promotion/rollback, while
P5.5 monitors promoted market champions. P5.6 combines those governance layers
into one deterministic, read-only operating decision per market and one
aggregate decision across spread and total.

The control plane never promotes or rolls back a market champion. All mutations
remain in P5.4's owner-only endpoints and still require exact confirmation.
"""
from __future__ import annotations

from typing import Any

import p54_game_market_calibration as p54
import p55_game_market_calibration_guard as p55

MODEL_NAME = "p5.6-game-market-calibration-control-plane"
MODEL_VERSION = "p56-market-control-plane-v1"


def _candidate_id(challenger: dict[str, Any]) -> str | None:
    candidate = challenger.get("candidate")
    if not isinstance(candidate, dict):
        return None
    value = str(candidate.get("candidateId") or "").strip()
    return value or None


def build_market_control_plane(
    market: str,
    challenger: dict[str, Any],
    champion: dict[str, Any],
    guard: dict[str, Any],
) -> dict[str, Any]:
    """Combine P5.4 challenger/champion and P5.5 guard into one market decision."""
    market = p54._market(market)  # noqa: SLF001 - canonical market validation
    promotion_gate = (
        challenger.get("promotionGate")
        if isinstance(challenger.get("promotionGate"), dict)
        else {}
    )
    rollback_gate = (
        guard.get("rollbackGate")
        if isinstance(guard.get("rollbackGate"), dict)
        else {}
    )

    candidate_id = _candidate_id(challenger)
    champion_candidate = str(champion.get("candidateId") or "").strip() or None
    champion_applied = champion.get("applied") is True
    challenger_review = challenger.get("state") == "review"
    challenger_eligible = promotion_gate.get("eligible") is True
    guard_state = str(guard.get("state") or "unavailable")
    rollback_recommended = rollback_gate.get("recommended") is True

    promote_ready = bool(
        not champion_applied
        and candidate_id
        and challenger_review
        and challenger_eligible
    )
    rollback_ready = bool(champion_applied and rollback_recommended)

    if rollback_ready:
        state = "rollback-review"
        action = "REVIEW_MARKET_ROLLBACK"
        message = (
            f"The active {market} calibration champion crossed P5.5 post-promotion "
            "guardrails. Owner review is required before rollback."
        )
    elif champion_applied:
        if guard_state == "collecting":
            state = "champion-collecting"
            action = "KEEP_AND_COLLECT"
            message = (
                f"The promoted {market} calibration champion is active and still "
                "collecting protected post-promotion evidence."
            )
        elif guard_state == "healthy":
            state = "champion-healthy"
            action = "KEEP_MARKET_CHAMPION"
            message = (
                f"The promoted {market} calibration champion remains inside P5.5 "
                "post-promotion guardrails."
            )
        else:
            state = "champion-monitor"
            action = "MONITOR_MARKET_CHAMPION"
            message = (
                f"A promoted {market} calibration champion is active; no automatic "
                "promotion or rollback action is permitted."
            )
    elif promote_ready:
        state = "promotion-review"
        action = "REVIEW_MARKET_PROMOTION"
        message = (
            f"The current {market} challenger clears all P5.4 market-specific "
            "promotion gates. An owner may explicitly review and confirm promotion."
        )
    elif challenger.get("state") == "collecting":
        state = "challenger-collecting"
        action = "COLLECT_MORE_MARKET_RESULTS"
        message = (
            f"The {market} challenger needs more graded market evidence before "
            "promotion can be considered."
        )
    else:
        state = "baseline-monitor"
        action = "KEEP_MARKET_BASELINE"
        message = (
            f"The baseline {market} calibration remains active; no challenger "
            "currently clears every promotion gate."
        )

    blockers: list[str] = []
    if not champion_applied and not promote_ready:
        if not candidate_id:
            blockers.append("no_current_candidate")
        if not challenger_review:
            blockers.append("challenger_not_in_review_state")
        if not challenger_eligible:
            blockers.append("p54_market_promotion_gate_not_eligible")

    return {
        "available": bool(
            challenger.get("available") is not False
            and champion.get("available") is not False
            and guard.get("available") is not False
        ),
        "market": market,
        "state": state,
        "recommendedAction": action,
        "message": message,
        "candidateId": candidate_id,
        "championCandidateId": champion_candidate,
        "championApplied": champion_applied,
        "promoteReady": promote_ready,
        "rollbackReady": rollback_ready,
        "blockers": blockers,
        "evidence": {
            "challengerState": challenger.get("state"),
            "challengerGradedSamples": challenger.get("gradedSamples", 0),
            "promotionEligible": challenger_eligible,
            "guardState": guard_state,
            "guardGradedSamples": guard.get("gradedSamples", 0),
            "rollbackRecommended": rollback_recommended,
        },
        "commands": {
            "promotion": {
                "endpoint": "/api/game-market-calibration/promote",
                "allowed": promote_ready,
                "market": market,
                "candidateId": candidate_id if promote_ready else None,
                "confirmation": p54.PROMOTE_CONFIRMATION,
                "ownerRoleRequired": True,
            },
            "rollback": {
                "endpoint": "/api/game-market-calibration/rollback",
                "allowed": champion_applied,
                "recommended": rollback_ready,
                "market": market,
                "confirmation": p54.ROLLBACK_CONFIRMATION,
                "ownerRoleRequired": True,
            },
        },
    }


def build_control_plane(
    calibration: dict[str, Any],
    guard: dict[str, Any],
) -> dict[str, Any]:
    """Return the canonical aggregate operating decision across spread and total."""
    calibration_markets = (
        calibration.get("markets") if isinstance(calibration.get("markets"), dict) else {}
    )
    champions = (
        calibration.get("champions") if isinstance(calibration.get("champions"), dict) else {}
    )
    guard_markets = guard.get("markets") if isinstance(guard.get("markets"), dict) else {}

    markets = {
        market: build_market_control_plane(
            market,
            calibration_markets.get(market) or {"available": False, "state": "unavailable"},
            champions.get(market) or {"available": False, "state": "baseline", "applied": False},
            guard_markets.get(market) or {"available": False, "state": "unavailable"},
        )
        for market in p54.MARKETS
    }

    rollback_review_markets = [
        market for market, report in markets.items() if report.get("rollbackReady") is True
    ]
    promotion_review_markets = [
        market for market, report in markets.items() if report.get("promoteReady") is True
    ]
    active_markets = [
        market for market, report in markets.items() if report.get("championApplied") is True
    ]
    collecting_markets = [
        market
        for market, report in markets.items()
        if report.get("state") in {"challenger-collecting", "champion-collecting"}
    ]
    healthy_markets = [
        market for market, report in markets.items() if report.get("state") == "champion-healthy"
    ]

    if rollback_review_markets:
        state = "rollback-review"
        action = "REVIEW_MARKET_ROLLBACKS"
        message = "One or more promoted market champions require owner rollback review."
    elif promotion_review_markets:
        state = "promotion-review"
        action = "REVIEW_MARKET_PROMOTIONS"
        message = "One or more market challengers clear every P5.4 promotion gate."
    elif collecting_markets:
        state = "collecting"
        action = "COLLECT_MORE_MARKET_RESULTS"
        message = "At least one market governance path is still collecting protected evidence."
    elif healthy_markets:
        state = "champions-healthy"
        action = "KEEP_HEALTHY_MARKET_CHAMPIONS"
        message = "All active promoted market champions currently remain inside guardrails."
    elif active_markets:
        state = "champions-monitor"
        action = "MONITOR_MARKET_CHAMPIONS"
        message = "Market champions are active and remain under read-only governance monitoring."
    else:
        state = "baseline-monitor"
        action = "KEEP_MARKET_BASELINES"
        message = "Spread and total remain on baseline calibration with no current mutation required."

    return {
        "available": bool(
            calibration.get("available") is not False and guard.get("available") is not False
        ),
        "model": MODEL_NAME,
        "modelVersion": MODEL_VERSION,
        "state": state,
        "recommendedAction": action,
        "message": message,
        "markets": markets,
        "promotionReviewMarkets": promotion_review_markets,
        "rollbackReviewMarkets": rollback_review_markets,
        "activeChampionMarkets": active_markets,
        "collectingMarkets": collecting_markets,
        "healthyChampionMarkets": healthy_markets,
        "safetyContract": {
            "readOnly": True,
            "marketIsolated": True,
            "providerRequests": 0,
            "writesPromotionRegistry": False,
            "automaticPromotion": False,
            "automaticRollback": False,
            "ownerConfirmationRequired": True,
            "changesModelProbabilities": False,
            "changesSelectedSide": False,
            "changesActionabilityThresholds": False,
            "changesBankrollPolicy": False,
            "placesBets": False,
        },
    }


def build_production_control_plane() -> dict[str, Any]:
    """Read P5.4/P5.5 production governance and return the P5.6 control plane."""
    calibration = p54.build_production_report()
    guard = p55.build_production_report()
    return build_control_plane(calibration, guard)
