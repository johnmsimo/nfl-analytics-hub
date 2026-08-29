"""P5.2 unified game-calibration control plane.

P4.9 evaluates challengers, P5.0 owns explicit promotion/rollback, and P5.1
monitors promoted champions. P5.2 combines those independent governance layers
into one deterministic read-only operational decision so product surfaces and
operators do not have to reimplement promotion/rollback readiness client-side.

The control plane never promotes or rolls back a model. All mutations remain in
the existing P5.0 owner-only endpoints and still require exact confirmation.
"""
from __future__ import annotations

from typing import Any

import p49_game_calibration as p49
import p50_game_calibration_promotion as p50
import p51_game_calibration_guard as p51

MODEL_NAME = "p5.2-game-calibration-control-plane"
MODEL_VERSION = "p52-control-plane-v1"


def _candidate_id(challenger: dict[str, Any]) -> str | None:
    candidate = challenger.get("candidate")
    if not isinstance(candidate, dict):
        return None
    value = str(candidate.get("candidateId") or "").strip()
    return value or None


def build_control_plane(
    challenger: dict[str, Any],
    champion_status: dict[str, Any],
    guard: dict[str, Any],
) -> dict[str, Any]:
    """Combine P4.9/P5.0/P5.1 into one fail-closed operator decision."""
    champion = (
        champion_status.get("champion")
        if isinstance(champion_status.get("champion"), dict)
        else {}
    )
    promotion_review = (
        champion_status.get("promotionReview")
        if isinstance(champion_status.get("promotionReview"), dict)
        else {}
    )
    rollback_gate = (
        guard.get("rollbackGate") if isinstance(guard.get("rollbackGate"), dict) else {}
    )

    candidate_id = _candidate_id(challenger)
    champion_candidate = str(champion.get("candidateId") or "").strip() or None
    champion_applied = champion.get("applied") is True
    challenger_review = challenger.get("state") == "review"
    challenger_eligible = (
        (challenger.get("promotionGate") or {}).get("eligible") is True
        if isinstance(challenger.get("promotionGate"), dict)
        else False
    )
    p50_eligible = promotion_review.get("eligible") is True
    guard_state = str(guard.get("state") or "unavailable")
    rollback_recommended = rollback_gate.get("recommended") is True

    promote_ready = bool(
        not champion_applied
        and candidate_id
        and challenger_review
        and challenger_eligible
        and p50_eligible
    )
    rollback_ready = bool(champion_applied and rollback_recommended)

    if rollback_ready:
        state = "rollback-review"
        action = "REVIEW_ROLLBACK"
        message = (
            "The active promoted champion has crossed P5.1 post-promotion guardrails. "
            "Owner review is required before any rollback."
        )
    elif champion_applied:
        if guard_state == "collecting":
            state = "champion-collecting"
            action = "KEEP_AND_COLLECT"
            message = "A promoted champion is active and is still collecting protected post-promotion evidence."
        elif guard_state == "healthy":
            state = "champion-healthy"
            action = "KEEP_CHAMPION"
            message = "The promoted champion remains inside P5.1 post-promotion guardrails."
        else:
            state = "champion-monitor"
            action = "MONITOR_CHAMPION"
            message = "A promoted champion is active; no automatic promotion or rollback action is permitted."
    elif promote_ready:
        state = "promotion-review"
        action = "REVIEW_PROMOTION"
        message = (
            "The current challenger clears P4.9 and P5.0 promotion gates. "
            "An owner may explicitly review and confirm promotion."
        )
    elif challenger.get("state") == "collecting":
        state = "challenger-collecting"
        action = "COLLECT_MORE_RESULTS"
        message = "The calibration challenger needs more graded evidence before promotion can be considered."
    else:
        state = "baseline-monitor"
        action = "KEEP_BASELINE"
        message = "The baseline champion remains active; no challenger currently clears every promotion gate."

    blockers: list[str] = []
    if not champion_applied and not promote_ready:
        if not candidate_id:
            blockers.append("no_current_candidate")
        if not challenger_review:
            blockers.append("challenger_not_in_review_state")
        if not challenger_eligible:
            blockers.append("p49_promotion_gate_not_eligible")
        if not p50_eligible:
            blockers.append("p50_moneyline_promotion_gate_not_eligible")

    return {
        "available": bool(
            challenger.get("available") is not False
            and champion_status.get("available") is not False
            and guard.get("available") is not False
        ),
        "model": MODEL_NAME,
        "modelVersion": MODEL_VERSION,
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
            "p49Eligible": challenger_eligible,
            "p50Eligible": p50_eligible,
            "guardState": guard_state,
            "guardGradedSamples": guard.get("gradedSamples", 0),
            "rollbackRecommended": rollback_recommended,
        },
        "commands": {
            "promotion": {
                "endpoint": "/api/game-calibration/promote",
                "allowed": promote_ready,
                "candidateId": candidate_id if promote_ready else None,
                "confirmation": p50.PROMOTE_CONFIRMATION,
                "ownerRoleRequired": True,
            },
            "rollback": {
                "endpoint": "/api/game-calibration/rollback",
                "allowed": champion_applied,
                "recommended": rollback_ready,
                "confirmation": p50.ROLLBACK_CONFIRMATION,
                "ownerRoleRequired": True,
            },
        },
        "safetyContract": {
            "readOnly": True,
            "providerRequests": 0,
            "writesPromotionRegistry": False,
            "automaticPromotion": False,
            "automaticRollback": False,
            "ownerConfirmationRequired": True,
            "changesModelProbabilities": False,
            "changesActionabilityThresholds": False,
            "changesBankrollPolicy": False,
            "placesBets": False,
        },
    }


def build_production_control_plane() -> dict[str, Any]:
    """Read production governance layers and return the canonical P5.2 decision."""
    challenger = p49.build_production_report()
    champion_status = p50.build_status()
    guard = p51.build_production_report()
    return build_control_plane(challenger, champion_status, guard)
