"""P3.9 versioned calibration challenger and promotion governance.

P3.8 measures production calibration from immutable P3.7 decision receipts.
P3.9 may *propose* a deterministic challenger calibration, but it never mutates
production probabilities, decision thresholds, or any persisted receipt.

The challenger is fit on the older portion of graded receipts and evaluated on
a newer chronological holdout. Promotion eligibility is only a review signal;
there is intentionally no automatic apply path in this module.
"""
from __future__ import annotations

import hashlib
import math
import os
from typing import Any, Iterable

import decision_ledger

MODEL_NAME = "p3.9-calibration-challenger"
RECEIPT_LIMIT = 2000


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
        "minGradedSamples": _env_int("P39_MIN_GRADED_SAMPLES", 80, 30, 5000),
        "minValidationSamples": _env_int("P39_MIN_VALIDATION_SAMPLES", 24, 10, 2000),
        "trainFraction": _env_float("P39_TRAIN_FRACTION", 0.70, 0.50, 0.85),
        "minBrierImprovement": _env_float("P39_MIN_BRIER_IMPROVEMENT", 0.005, 0.0, 0.10),
        "maxEceRegression": _env_float("P39_MAX_ECE_REGRESSION", 0.01, 0.0, 0.10),
        "receiptWindowLimit": RECEIPT_LIMIT,
    }


def _probability(receipt: dict[str, Any]) -> float | None:
    result = receipt.get("result") if isinstance(receipt.get("result"), dict) else {}
    release = receipt.get("release") if isinstance(receipt.get("release"), dict) else {}
    for value in (result.get("probability"), release.get("consensusProb"), release.get("modelProb")):
        if isinstance(value, (int, float)):
            return max(0.001, min(0.999, float(value)))
    return None


def _sample(receipt: dict[str, Any]) -> dict[str, Any] | None:
    grade = str(receipt.get("grade") or "").lower()
    probability = _probability(receipt)
    if grade not in {"win", "loss"} or probability is None:
        return None
    return {
        "receiptId": str(receipt.get("receiptId") or ""),
        "releasedAt": str(receipt.get("releasedAt") or ""),
        "probability": probability,
        "outcome": 1.0 if grade == "win" else 0.0,
    }


def _sigmoid(value: float) -> float:
    if value >= 0:
        z = math.exp(-value)
        return 1.0 / (1.0 + z)
    z = math.exp(value)
    return z / (1.0 + z)


def calibrate_probability(probability: float, *, slope: float, intercept: float) -> float:
    p = max(0.001, min(0.999, float(probability)))
    logit = math.log(p / (1.0 - p))
    return max(0.001, min(0.999, _sigmoid(intercept + slope * logit)))


def _ece(samples: list[tuple[float, float]], bins: int = 10) -> float | None:
    if not samples:
        return None
    total = len(samples)
    error = 0.0
    for idx in range(bins):
        low, high = idx / bins, (idx + 1) / bins
        bucket = [row for row in samples if low <= row[0] < high or (idx == bins - 1 and row[0] == 1.0)]
        if not bucket:
            continue
        avg_p = sum(row[0] for row in bucket) / len(bucket)
        avg_y = sum(row[1] for row in bucket) / len(bucket)
        error += len(bucket) / total * abs(avg_p - avg_y)
    return round(error, 6)


def _metrics(samples: list[dict[str, Any]], *, slope: float = 1.0, intercept: float = 0.0) -> dict[str, Any]:
    pairs = [
        (calibrate_probability(row["probability"], slope=slope, intercept=intercept), row["outcome"])
        for row in samples
    ]
    if not pairs:
        return {"samples": 0, "brier": None, "ece": None, "avgProbability": None, "hitRate": None}
    brier = sum((p - y) ** 2 for p, y in pairs) / len(pairs)
    return {
        "samples": len(pairs),
        "brier": round(brier, 6),
        "ece": _ece(pairs),
        "avgProbability": round(sum(p for p, _ in pairs) / len(pairs), 6),
        "hitRate": round(sum(y for _, y in pairs) / len(pairs), 6),
    }


def _fit_candidate(train: list[dict[str, Any]]) -> tuple[float, float, dict[str, Any]]:
    best: tuple[tuple[float, float, float], float, float, dict[str, Any]] | None = None
    slopes = [round(0.50 + 0.05 * idx, 2) for idx in range(21)]
    intercepts = [round(-0.30 + 0.05 * idx, 2) for idx in range(13)]
    for slope in slopes:
        for intercept in intercepts:
            metrics = _metrics(train, slope=slope, intercept=intercept)
            distance = abs(slope - 1.0) + abs(intercept)
            rank = (float(metrics["brier"]), float(metrics["ece"] or 0.0), distance)
            if best is None or rank < best[0]:
                best = (rank, slope, intercept, metrics)
    assert best is not None
    return best[1], best[2], best[3]


def _candidate_id(samples: list[dict[str, Any]], slope: float, intercept: float) -> str:
    material = "|".join(row["receiptId"] for row in samples)
    material += f"|{slope:.4f}|{intercept:.4f}|{MODEL_NAME}"
    return "p39-" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


def build_candidate_report(
    receipts: Iterable[dict[str, Any]],
    *,
    min_samples: int | None = None,
    min_validation_samples: int | None = None,
    train_fraction: float | None = None,
    min_brier_improvement: float | None = None,
    max_ece_regression: float | None = None,
) -> dict[str, Any]:
    active = policy()
    min_samples = int(active["minGradedSamples"] if min_samples is None else min_samples)
    min_validation_samples = int(active["minValidationSamples"] if min_validation_samples is None else min_validation_samples)
    train_fraction = float(active["trainFraction"] if train_fraction is None else train_fraction)
    min_brier_improvement = float(active["minBrierImprovement"] if min_brier_improvement is None else min_brier_improvement)
    max_ece_regression = float(active["maxEceRegression"] if max_ece_regression is None else max_ece_regression)

    receipt_list = list(receipts)
    samples = [sample for receipt in receipt_list if (sample := _sample(receipt)) is not None]
    samples.sort(key=lambda row: (row["releasedAt"], row["receiptId"]))

    base = {
        "available": True,
        "model": MODEL_NAME,
        "autoApply": False,
        "productionApplied": False,
        "receiptCount": len(receipt_list),
        "gradedSamples": len(samples),
        "policy": {
            "minGradedSamples": min_samples,
            "minValidationSamples": min_validation_samples,
            "trainFraction": train_fraction,
            "minBrierImprovement": min_brier_improvement,
            "maxEceRegression": max_ece_regression,
            "receiptWindowLimit": active["receiptWindowLimit"],
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
                "reason": "insufficient_samples",
            },
        }

    validation_count = max(min_validation_samples, int(round(len(samples) * (1.0 - train_fraction))))
    validation_count = min(validation_count, len(samples) - 1)
    train = samples[:-validation_count]
    validation = samples[-validation_count:]
    if not train or len(validation) < min_validation_samples:
        return {
            **base,
            "state": "collecting",
            "candidate": None,
            "promotionGate": {
                "eligible": False,
                "requiresHumanReview": True,
                "automaticApply": False,
                "reason": "insufficient_validation_samples",
            },
        }

    slope, intercept, train_candidate = _fit_candidate(train)
    train_champion = _metrics(train)
    validation_champion = _metrics(validation)
    validation_candidate = _metrics(validation, slope=slope, intercept=intercept)
    brier_improvement = round(float(validation_champion["brier"]) - float(validation_candidate["brier"]), 6)
    champion_ece = float(validation_champion["ece"] or 0.0)
    candidate_ece = float(validation_candidate["ece"] or 0.0)
    ece_regression = round(candidate_ece - champion_ece, 6)
    identity = slope == 1.0 and intercept == 0.0
    eligible = (
        not identity
        and brier_improvement >= min_brier_improvement
        and ece_regression <= max_ece_regression
    )

    candidate = {
        "candidateId": _candidate_id(train, slope, intercept),
        "family": "logit-affine",
        "parameters": {"slope": slope, "intercept": intercept},
        "train": {"champion": train_champion, "challenger": train_candidate},
        "validation": {"champion": validation_champion, "challenger": validation_candidate},
        "validationBrierImprovement": brier_improvement,
        "validationEceRegression": ece_regression,
        "trainSamples": len(train),
        "validationSamples": len(validation),
        "validationIsForwardHoldout": True,
    }
    return {
        **base,
        "state": "review" if eligible else "rejected",
        "candidate": candidate,
        "promotionGate": {
            "eligible": eligible,
            "requiresHumanReview": True,
            "automaticApply": False,
            "reason": "candidate_clears_holdout_gate" if eligible else "candidate_does_not_clear_holdout_gate",
        },
    }


def build_production_report() -> dict[str, Any]:
    status = decision_ledger.ledger_status()
    if not status.get("available"):
        return {
            "available": False,
            "model": MODEL_NAME,
            "state": "unavailable",
            "autoApply": False,
            "productionApplied": False,
            "ledger": status,
        }
    report = build_candidate_report(decision_ledger.list_receipts(limit=RECEIPT_LIMIT))
    report["ledger"] = status
    return report
