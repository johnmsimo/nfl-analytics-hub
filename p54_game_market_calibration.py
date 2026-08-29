"""P5.4 market-specific spread/total calibration challengers and promotion.

P5.0 safely applies an explicitly promoted calibration champion to P4.0
moneyline probabilities. P5.4 extends the same human-governed pattern to P4.1
spread and total selected-side probabilities, but fits each market separately
from immutable P4.4 receipts so one market can never borrow calibration evidence
from another.

No market challenger is applied automatically. Promotion and rollback are
append-only, owner-confirmed operations. Registry failure always falls back to
the unchanged P4.1 probability.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from typing import Any, Iterable

import sqlalchemy as sa
from sqlalchemy.exc import SQLAlchemyError

from database import db
import p44_game_decision_ledger as p44
import p49_game_calibration as p49

MODEL_NAME = "p5.4-game-market-calibration"
MODEL_VERSION = "p54-market-calibration-v1"
BASE_MODEL_VERSION = "p41-pricing-v1"
MARKETS = ("spread", "total")
PROMOTE_CONFIRMATION = "PROMOTE_GAME_MARKET_CALIBRATION"
ROLLBACK_CONFIRMATION = "ROLLBACK_GAME_MARKET_CALIBRATION"
RECEIPT_LIMIT = 2000
EVENT_LIMIT = 200

promotion_events = sa.Table(
    "game_market_calibration_promotion_events",
    db.metadata,
    sa.Column("event_id", sa.String(32), primary_key=True),
    sa.Column("market_key", sa.String(24), nullable=False, index=True),
    sa.Column("action", sa.String(16), nullable=False, index=True),
    sa.Column("candidate_id", sa.String(64), index=True),
    sa.Column("family", sa.String(32)),
    sa.Column("slope", sa.Float),
    sa.Column("intercept", sa.Float),
    sa.Column("base_model_version", sa.String(64), nullable=False),
    sa.Column("approved_by", sa.String(128), nullable=False),
    sa.Column("governance_fingerprint", sa.String(64), nullable=False),
    sa.Column("governance_payload", sa.JSON, nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, index=True),
)


def _now() -> datetime:
    return datetime.now(UTC)


def _rollback() -> None:
    try:
        db.session.rollback()
    except Exception:  # noqa: BLE001
        pass


def _env_int(name: str, default: int, low: int, high: int) -> int:
    value = int(os.environ.get(name, default))
    if not low <= value <= high:
        raise ValueError(f"{name} must be between {low} and {high}")
    return value


def _env_float(name: str, default: float, low: float, high: float) -> float:
    value = float(os.environ.get(name, default))
    if not low <= value <= high:
        raise ValueError(f"{name} must be between {low} and {high}")
    return value


def policy() -> dict[str, Any]:
    return {
        "minGradedSamplesPerMarket": _env_int("P54_MIN_GRADED_SAMPLES", 50, 30, 2000),
        "minValidationSamplesPerMarket": _env_int("P54_MIN_VALIDATION_SAMPLES", 15, 10, 1000),
        "minMarketBenchmarkSamples": _env_int("P54_MIN_MARKET_BENCHMARK_SAMPLES", 10, 5, 1000),
        "trainFraction": _env_float("P54_TRAIN_FRACTION", 0.70, 0.50, 0.85),
        "minBrierImprovement": _env_float("P54_MIN_BRIER_IMPROVEMENT", 0.003, 0.0, 0.10),
        "maxEceRegression": _env_float("P54_MAX_ECE_REGRESSION", 0.015, 0.0, 0.10),
        "maxMarketSkillRegression": _env_float("P54_MAX_MARKET_SKILL_REGRESSION", 0.01, 0.0, 0.10),
        "selectedProbabilityFloor": 0.5,
        "selectedProbabilityCeiling": 0.999,
        "receiptWindowLimit": RECEIPT_LIMIT,
    }


def _market(value: Any) -> str:
    market = str(value or "").lower().strip()
    if market not in MARKETS:
        raise ValueError(f"market must be one of {', '.join(MARKETS)}")
    return market


def _fingerprint(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _candidate_id(market: str, train: list[dict[str, Any]], slope: float, intercept: float) -> str:
    material = market + "|" + "|".join(row["receiptId"] for row in train)
    material += f"|{slope:.4f}|{intercept:.4f}|{MODEL_NAME}|{MODEL_VERSION}"
    return "p54-" + market[:2] + "-" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


def _market_samples(receipts: Iterable[dict[str, Any]], market: str) -> list[dict[str, Any]]:
    wanted = _market(market)
    samples = [
        sample
        for receipt in receipts
        if (sample := p49._sample(receipt)) is not None  # noqa: SLF001 - canonical immutable receipt parser
        and sample.get("market") == wanted
    ]
    samples.sort(key=lambda row: (row["releasedAt"], row["receiptId"]))
    return samples


def build_market_candidate_report(
    receipts: Iterable[dict[str, Any]],
    market: str,
    *,
    min_samples: int | None = None,
    min_validation_samples: int | None = None,
    min_market_benchmark_samples: int | None = None,
    train_fraction: float | None = None,
    min_brier_improvement: float | None = None,
    max_ece_regression: float | None = None,
    max_market_skill_regression: float | None = None,
) -> dict[str, Any]:
    """Fit one market-only challenger and evaluate it on a forward holdout."""
    market = _market(market)
    active = policy()
    min_samples = int(active["minGradedSamplesPerMarket"] if min_samples is None else min_samples)
    min_validation_samples = int(active["minValidationSamplesPerMarket"] if min_validation_samples is None else min_validation_samples)
    min_market_benchmark_samples = int(active["minMarketBenchmarkSamples"] if min_market_benchmark_samples is None else min_market_benchmark_samples)
    train_fraction = float(active["trainFraction"] if train_fraction is None else train_fraction)
    min_brier_improvement = float(active["minBrierImprovement"] if min_brier_improvement is None else min_brier_improvement)
    max_ece_regression = float(active["maxEceRegression"] if max_ece_regression is None else max_ece_regression)
    max_market_skill_regression = float(active["maxMarketSkillRegression"] if max_market_skill_regression is None else max_market_skill_regression)

    receipt_list = list(receipts)
    samples = _market_samples(receipt_list, market)
    base = {
        "available": True,
        "model": MODEL_NAME,
        "modelVersion": MODEL_VERSION,
        "market": market,
        "receiptCount": len(receipt_list),
        "gradedSamples": len(samples),
        "productionApplied": current_champion(market).get("applied") is True,
        "automaticApply": False,
        "policy": {
            "minGradedSamples": min_samples,
            "minValidationSamples": min_validation_samples,
            "minMarketBenchmarkSamples": min_market_benchmark_samples,
            "trainFraction": train_fraction,
            "minBrierImprovement": min_brier_improvement,
            "maxEceRegression": max_ece_regression,
            "maxMarketSkillRegression": max_market_skill_regression,
        },
        "safetyContract": {
            "marketIsolatedTraining": True,
            "forwardHoldout": True,
            "providerRequests": 0,
            "writesLedger": False,
            "writesTracker": False,
            "automaticPromotion": False,
            "changesActionabilityThresholds": False,
            "changesBankrollPolicy": False,
            "placesBets": False,
        },
    }
    if len(samples) < min_samples:
        return {
            **base,
            "state": "collecting",
            "candidate": None,
            "promotionGate": {
                "eligible": False,
                "requiresHumanReview": True,
                "automaticApply": False,
                "reason": "insufficient_market_samples",
                "checks": {},
            },
        }

    validation_target = max(min_validation_samples, int(round(len(samples) * (1.0 - train_fraction))))
    validation_target = min(validation_target, len(samples) - 1)
    train, validation, boundary, forward_integrity = p49._split_forward_holdout(  # noqa: SLF001
        samples, validation_target
    )
    if not train or len(validation) < min_validation_samples or not forward_integrity:
        return {
            **base,
            "state": "collecting",
            "candidate": None,
            "promotionGate": {
                "eligible": False,
                "requiresHumanReview": True,
                "automaticApply": False,
                "reason": "insufficient_leakage_safe_market_holdout",
                "checks": {
                    "forwardHoldoutIntegrity": forward_integrity,
                    "validationSampleFloor": len(validation) >= min_validation_samples,
                },
            },
        }

    slope, intercept, train_challenger = p49._fit_candidate(train)  # noqa: SLF001
    train_champion = p49._metrics(train)  # noqa: SLF001
    validation_champion = p49._metrics(validation)  # noqa: SLF001
    validation_challenger = p49._metrics(validation, slope=slope, intercept=intercept)  # noqa: SLF001
    brier_improvement = float(validation_champion["brier"]) - float(validation_challenger["brier"])
    ece_delta = float(validation_challenger["ece"] or 0.0) - float(validation_champion["ece"] or 0.0)
    market_skill_delta = None
    if isinstance(validation_champion.get("brierSkillVsMarket"), (int, float)) and isinstance(validation_challenger.get("brierSkillVsMarket"), (int, float)):
        market_skill_delta = float(validation_challenger["brierSkillVsMarket"]) - float(validation_champion["brierSkillVsMarket"])
    non_identity = abs(float(slope) - 1.0) > 1e-9 or abs(float(intercept)) > 1e-9
    checks = {
        "marketIsolatedTraining": all(row.get("market") == market for row in train + validation),
        "forwardHoldoutIntegrity": forward_integrity,
        "validationSampleFloor": len(validation) >= min_validation_samples,
        "marketBenchmarkSampleFloor": int(validation_challenger.get("marketBenchmarkSamples") or 0) >= min_market_benchmark_samples,
        "nonIdentityCandidate": non_identity,
        "brierImprovement": brier_improvement >= min_brier_improvement,
        "eceRegressionBounded": ece_delta <= max_ece_regression,
        "marketSkillRegressionBounded": market_skill_delta is not None and market_skill_delta >= -max_market_skill_regression,
    }
    eligible = all(checks.values())
    candidate = {
        "candidateId": _candidate_id(market, train, slope, intercept),
        "market": market,
        "family": "logit-affine",
        "parameters": {"slope": slope, "intercept": intercept},
        "trainSamples": len(train),
        "validationSamples": len(validation),
        "validationBoundaryReleasedAt": boundary,
        "validationIsForwardHoldout": forward_integrity,
        "validationPreventsBatchAndGameLeakage": forward_integrity,
        "trainChampion": train_champion,
        "trainChallenger": train_challenger,
        "validationChampion": validation_champion,
        "validationChallenger": validation_challenger,
        "validationBrierImprovement": round(brier_improvement, 6),
        "validationEceDelta": round(ece_delta, 6),
        "validationMarketSkillDelta": round(market_skill_delta, 6) if market_skill_delta is not None else None,
    }
    return {
        **base,
        "state": "review" if eligible else "rejected",
        "candidate": candidate,
        "promotionGate": {
            "eligible": eligible,
            "requiresHumanReview": True,
            "automaticApply": False,
            "reason": "eligible_for_human_review" if eligible else "market_challenger_failed_guardrails",
            "checks": checks,
            "failedChecks": [key for key, passed in checks.items() if not passed],
        },
    }


def _event_public(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "eventId": row.get("event_id"),
        "market": row.get("market_key"),
        "action": row.get("action"),
        "candidateId": row.get("candidate_id"),
        "family": row.get("family"),
        "parameters": {"slope": row.get("slope"), "intercept": row.get("intercept")},
        "baseModelVersion": row.get("base_model_version"),
        "approvedBy": row.get("approved_by"),
        "governanceFingerprint": row.get("governance_fingerprint"),
        "createdAt": row.get("created_at").isoformat() if row.get("created_at") else None,
    }


def list_events(*, market: str | None = None, limit: int = EVENT_LIMIT) -> list[dict[str, Any]]:
    stmt = sa.select(promotion_events)
    if market is not None:
        stmt = stmt.where(promotion_events.c.market_key == _market(market))
    stmt = stmt.order_by(promotion_events.c.created_at.desc(), promotion_events.c.event_id.desc()).limit(max(1, min(int(limit), EVENT_LIMIT)))
    try:
        rows = db.session.execute(stmt).mappings().all()
    except (RuntimeError, SQLAlchemyError):
        _rollback()
        return []
    return [_event_public(dict(row)) for row in rows]


def current_champion(market: str) -> dict[str, Any]:
    market = _market(market)
    try:
        row = db.session.execute(
            sa.select(promotion_events)
            .where(promotion_events.c.market_key == market)
            .order_by(promotion_events.c.created_at.desc(), promotion_events.c.event_id.desc())
            .limit(1)
        ).mappings().first()
    except (RuntimeError, SQLAlchemyError):
        _rollback()
        return {
            "available": False,
            "market": market,
            "state": "baseline",
            "applied": False,
            "candidateId": None,
            "parameters": {"slope": 1.0, "intercept": 0.0},
            "reason": "market_promotion_registry_unavailable",
        }
    if not row or str(row.action) != "promote":
        return {
            "available": True,
            "market": market,
            "state": "baseline",
            "applied": False,
            "candidateId": None,
            "parameters": {"slope": 1.0, "intercept": 0.0},
            "reason": "no_active_market_promotion" if not row else "market_rolled_back_to_baseline",
            "latestEvent": _event_public(dict(row)) if row else None,
        }
    if not isinstance(row.slope, (int, float)) or not isinstance(row.intercept, (int, float)):
        return {
            "available": True,
            "market": market,
            "state": "baseline",
            "applied": False,
            "candidateId": None,
            "parameters": {"slope": 1.0, "intercept": 0.0},
            "reason": "invalid_market_promotion",
            "latestEvent": _event_public(dict(row)),
        }
    return {
        "available": True,
        "market": market,
        "state": "promoted",
        "applied": True,
        "candidateId": row.candidate_id,
        "family": row.family,
        "parameters": {"slope": float(row.slope), "intercept": float(row.intercept)},
        "approvedBy": row.approved_by,
        "approvedAt": row.created_at.isoformat() if row.created_at else None,
        "governanceFingerprint": row.governance_fingerprint,
        "latestEvent": _event_public(dict(row)),
    }


def _build_event(action: str, market: str, *, actor: str, candidate: dict[str, Any] | None, governance: dict[str, Any]) -> dict[str, Any]:
    timestamp = _now()
    params = candidate.get("parameters") if candidate and isinstance(candidate.get("parameters"), dict) else {}
    candidate_id = candidate.get("candidateId") if candidate else None
    material = f"{timestamp.isoformat()}|{market}|{action}|{candidate_id or 'baseline'}|{actor}"
    return {
        "event_id": "p54-" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:24],
        "market_key": market,
        "action": action,
        "candidate_id": candidate_id,
        "family": candidate.get("family") if candidate else "identity",
        "slope": float(params["slope"]) if isinstance(params.get("slope"), (int, float)) else None,
        "intercept": float(params["intercept"]) if isinstance(params.get("intercept"), (int, float)) else None,
        "base_model_version": BASE_MODEL_VERSION,
        "approved_by": str(actor)[:128],
        "governance_fingerprint": _fingerprint(governance),
        "governance_payload": governance,
        "created_at": timestamp,
    }


def promote_candidate(
    market: str,
    candidate_id: str,
    *,
    confirmation: str,
    actor: str,
    persist: bool = True,
    report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    market = _market(market)
    if confirmation != PROMOTE_CONFIRMATION:
        return {"ok": False, "code": "CONFIRMATION_REQUIRED", "required": PROMOTE_CONFIRMATION}
    if not str(actor or "").strip():
        return {"ok": False, "code": "ACTOR_REQUIRED"}
    report = report or build_production_report()["markets"][market]
    candidate = report.get("candidate") if isinstance(report.get("candidate"), dict) else None
    gate = report.get("promotionGate") if isinstance(report.get("promotionGate"), dict) else {}
    if report.get("state") != "review" or gate.get("eligible") is not True or not candidate:
        return {"ok": False, "code": "PROMOTION_GATE_FAILED", "report": report}
    if str(candidate_id) != str(candidate.get("candidateId")):
        return {"ok": False, "code": "CANDIDATE_MISMATCH", "expectedCandidateId": candidate.get("candidateId")}
    current = current_champion(market) if persist else None
    if current and current.get("applied") and current.get("candidateId") == candidate_id:
        return {"ok": True, "idempotent": True, "champion": current}
    governance = {
        "market": market,
        "candidate": candidate,
        "promotionGate": gate,
        "automaticPromotion": False,
        "explicitOwnerConfirmation": True,
    }
    event = _build_event("promote", market, actor=actor, candidate=candidate, governance=governance)
    if not persist:
        return {"ok": True, "dryRun": True, "event": _event_public(event), "wouldApply": True}
    try:
        db.session.execute(promotion_events.insert().values(**event))
        db.session.commit()
    except (RuntimeError, SQLAlchemyError):
        _rollback()
        return {"ok": False, "code": "PROMOTION_WRITE_FAILED"}
    return {"ok": True, "event": _event_public(event), "champion": current_champion(market)}


def rollback_to_baseline(market: str, *, confirmation: str, actor: str, persist: bool = True) -> dict[str, Any]:
    market = _market(market)
    if confirmation != ROLLBACK_CONFIRMATION:
        return {"ok": False, "code": "CONFIRMATION_REQUIRED", "required": ROLLBACK_CONFIRMATION}
    if not str(actor or "").strip():
        return {"ok": False, "code": "ACTOR_REQUIRED"}
    current = current_champion(market)
    if not current.get("applied"):
        return {"ok": True, "idempotent": True, "champion": current}
    governance = {
        "market": market,
        "rollbackFromCandidateId": current.get("candidateId"),
        "rollbackFromParameters": current.get("parameters"),
        "automaticRollback": False,
        "explicitOwnerConfirmation": True,
    }
    event = _build_event("rollback", market, actor=actor, candidate=None, governance=governance)
    if not persist:
        return {"ok": True, "dryRun": True, "event": _event_public(event), "wouldApplyBaseline": True}
    try:
        db.session.execute(promotion_events.insert().values(**event))
        db.session.commit()
    except (RuntimeError, SQLAlchemyError):
        _rollback()
        return {"ok": False, "code": "ROLLBACK_WRITE_FAILED"}
    return {"ok": True, "event": _event_public(event), "champion": current_champion(market)}


def apply_to_selected_probability(
    market: str,
    probability: float,
    *,
    champion: dict[str, Any] | None = None,
) -> dict[str, Any]:
    market = _market(market)
    original = max(0.5, min(0.999, float(probability)))
    active = champion or current_champion(market)
    if not active.get("applied"):
        return {
            "probability": original,
            "rawProbability": original,
            "applied": False,
            "market": market,
            "candidateId": None,
            "modelVersion": MODEL_VERSION,
            "championState": active.get("state", "baseline"),
        }
    params = active.get("parameters") if isinstance(active.get("parameters"), dict) else {}
    slope, intercept = params.get("slope"), params.get("intercept")
    if not isinstance(slope, (int, float)) or not isinstance(intercept, (int, float)):
        return {
            "probability": original,
            "rawProbability": original,
            "applied": False,
            "market": market,
            "candidateId": None,
            "modelVersion": MODEL_VERSION,
            "championState": "invalid",
        }
    calibrated = p49.calibrate_probability(original, slope=float(slope), intercept=float(intercept))
    bounded = max(float(policy()["selectedProbabilityFloor"]), min(float(policy()["selectedProbabilityCeiling"]), calibrated))
    return {
        "probability": bounded,
        "rawProbability": original,
        "applied": True,
        "market": market,
        "candidateId": active.get("candidateId"),
        "family": active.get("family"),
        "parameters": {"slope": float(slope), "intercept": float(intercept)},
        "modelVersion": MODEL_VERSION,
        "championState": "promoted",
        "approvedAt": active.get("approvedAt"),
    }


def build_production_report() -> dict[str, Any]:
    status = p44.ledger_status()
    if status.get("available") is False:
        return {
            "available": False,
            "model": MODEL_NAME,
            "modelVersion": MODEL_VERSION,
            "state": "unavailable",
            "markets": {},
            "safetyContract": {"providerRequests": 0, "automaticPromotion": False},
        }
    receipts = p44.list_receipts(limit=RECEIPT_LIMIT)
    markets = {market: build_market_candidate_report(receipts, market) for market in MARKETS}
    states = {report.get("state") for report in markets.values()}
    if "review" in states:
        state = "review"
    elif states == {"collecting"}:
        state = "collecting"
    elif "rejected" in states:
        state = "monitor"
    else:
        state = "collecting"
    return {
        "available": True,
        "model": MODEL_NAME,
        "modelVersion": MODEL_VERSION,
        "state": state,
        "receiptCount": len(receipts),
        "markets": markets,
        "champions": {market: current_champion(market) for market in MARKETS},
        "recentEvents": list_events(limit=20),
        "safetyContract": {
            "providerRequests": 0,
            "marketIsolatedTraining": True,
            "automaticPromotion": False,
            "automaticRollback": False,
            "ownerConfirmationRequired": True,
            "changesActionabilityThresholds": False,
            "changesBankrollPolicy": False,
            "placesBets": False,
        },
    }
