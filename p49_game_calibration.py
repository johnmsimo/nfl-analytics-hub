"""P4.9 game calibration challenger and promotion governance.

P4.8 measures game-market calibration from immutable P4.4 receipts. P4.9 fits a
versioned logit-affine challenger on older graded game decisions and evaluates it
on a newer chronological holdout. A challenger may become *eligible for human
review*, but this module never applies calibration to P4.0 probabilities, changes
P4.1 actionability, changes P4.6 bankroll policy, or mutates any receipt.
"""
from __future__ import annotations

import hashlib
import math
import os
from typing import Any, Iterable

import p44_game_decision_ledger as p44

MODEL_NAME = "p4.9-game-calibration-challenger"
MODEL_VERSION = "p49-challenger-v1"
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
    """Return bounded governance thresholds for game calibration promotion."""
    return {
        "minGradedSamples": _env_int("P49_MIN_GRADED_SAMPLES", 80, 30, 5000),
        "minValidationSamples": _env_int(
            "P49_MIN_VALIDATION_SAMPLES", 24, 10, 2000
        ),
        "minMarketValidationSamples": _env_int(
            "P49_MIN_MARKET_VALIDATION_SAMPLES", 12, 5, 1000
        ),
        "trainFraction": _env_float("P49_TRAIN_FRACTION", 0.70, 0.50, 0.85),
        "minBrierImprovement": _env_float(
            "P49_MIN_BRIER_IMPROVEMENT", 0.005, 0.0, 0.10
        ),
        "maxEceRegression": _env_float(
            "P49_MAX_ECE_REGRESSION", 0.01, 0.0, 0.10
        ),
        "maxMarketSkillRegression": _env_float(
            "P49_MAX_MARKET_SKILL_REGRESSION", 0.005, 0.0, 0.10
        ),
        "receiptWindowLimit": RECEIPT_LIMIT,
    }


def _bounded_probability(value: Any) -> float | None:
    if not isinstance(value, (int, float)):
        return None
    return max(0.001, min(0.999, float(value)))


def _sample(receipt: dict[str, Any]) -> dict[str, Any] | None:
    grade = str(receipt.get("grade") or "").lower()
    if grade not in {"win", "loss"}:
        return None
    release = receipt.get("release") if isinstance(receipt.get("release"), dict) else {}
    result = receipt.get("result") if isinstance(receipt.get("result"), dict) else {}
    probability = _bounded_probability(
        result.get("probability")
        if result.get("probability") is not None
        else release.get("modelProbability")
    )
    if probability is None:
        return None
    return {
        "receiptId": str(receipt.get("receiptId") or ""),
        "releasedAt": str(receipt.get("releasedAt") or ""),
        "gameId": str(release.get("gameId") or ""),
        "probability": probability,
        "marketProbability": _bounded_probability(release.get("fairMarketProbability")),
        "outcome": 1.0 if grade == "win" else 0.0,
        "market": str(release.get("marketKey") or "unknown"),
    }


def _sigmoid(value: float) -> float:
    if value >= 0:
        z = math.exp(-value)
        return 1.0 / (1.0 + z)
    z = math.exp(value)
    return z / (1.0 + z)


def calibrate_probability(
    probability: float, *, slope: float, intercept: float
) -> float:
    """Apply a bounded logit-affine calibration transform."""
    p = max(0.001, min(0.999, float(probability)))
    logit = math.log(p / (1.0 - p))
    return max(0.001, min(0.999, _sigmoid(intercept + slope * logit)))


def _ece(pairs: list[tuple[float, float]], bins: int = 10) -> float | None:
    if not pairs:
        return None
    total = len(pairs)
    error = 0.0
    for idx in range(bins):
        low, high = idx / bins, (idx + 1) / bins
        bucket = [
            row
            for row in pairs
            if low <= row[0] < high or (idx == bins - 1 and row[0] == 1.0)
        ]
        if not bucket:
            continue
        avg_probability = sum(row[0] for row in bucket) / len(bucket)
        hit_rate = sum(row[1] for row in bucket) / len(bucket)
        error += len(bucket) / total * abs(avg_probability - hit_rate)
    return round(error, 6)


def _metrics(
    samples: list[dict[str, Any]], *, slope: float = 1.0, intercept: float = 0.0
) -> dict[str, Any]:
    pairs = [
        (
            calibrate_probability(
                row["probability"], slope=slope, intercept=intercept
            ),
            row["outcome"],
        )
        for row in samples
    ]
    if not pairs:
        return {
            "samples": 0,
            "brier": None,
            "ece": None,
            "avgProbability": None,
            "hitRate": None,
            "marketBenchmarkSamples": 0,
            "marketBrier": None,
            "brierSkillVsMarket": None,
        }
    brier = sum((probability - outcome) ** 2 for probability, outcome in pairs) / len(
        pairs
    )
    benchmark_rows = [
        row for row in samples if row.get("marketProbability") is not None
    ]
    market_brier = None
    paired_model_brier = None
    if benchmark_rows:
        market_brier = sum(
            (float(row["marketProbability"]) - row["outcome"]) ** 2
            for row in benchmark_rows
        ) / len(benchmark_rows)
        paired_model_brier = sum(
            (
                calibrate_probability(
                    row["probability"], slope=slope, intercept=intercept
                )
                - row["outcome"]
            )
            ** 2
            for row in benchmark_rows
        ) / len(benchmark_rows)
    return {
        "samples": len(pairs),
        "brier": round(brier, 6),
        "ece": _ece(pairs),
        "avgProbability": round(sum(row[0] for row in pairs) / len(pairs), 6),
        "hitRate": round(sum(row[1] for row in pairs) / len(pairs), 6),
        "marketBenchmarkSamples": len(benchmark_rows),
        "marketBrier": round(market_brier, 6) if market_brier is not None else None,
        "brierSkillVsMarket": (
            round(market_brier - paired_model_brier, 6)
            if market_brier is not None and paired_model_brier is not None
            else None
        ),
    }


def _fit_candidate(train: list[dict[str, Any]]) -> tuple[float, float, dict[str, Any]]:
    best: tuple[tuple[float, float, float], float, float, dict[str, Any]] | None = None
    slopes = [round(0.50 + 0.05 * idx, 2) for idx in range(21)]
    intercepts = [round(-0.30 + 0.05 * idx, 2) for idx in range(13)]
    for slope in slopes:
        for intercept in intercepts:
            metrics = _metrics(train, slope=slope, intercept=intercept)
            distance = abs(slope - 1.0) + abs(intercept)
            rank = (
                float(metrics["brier"]),
                float(metrics["ece"] or 0.0),
                distance,
            )
            if best is None or rank < best[0]:
                best = (rank, slope, intercept, metrics)
    assert best is not None
    return best[1], best[2], best[3]


def _candidate_id(
    train: list[dict[str, Any]], slope: float, intercept: float
) -> str:
    material = "|".join(row["receiptId"] for row in train)
    material += f"|{slope:.4f}|{intercept:.4f}|{MODEL_NAME}|{MODEL_VERSION}"
    return "p49-" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


def _market_segments(
    validation: list[dict[str, Any]], *, slope: float, intercept: float
) -> dict[str, dict[str, Any]]:
    markets = sorted({str(row.get("market") or "unknown") for row in validation})
    return {
        market: _metrics(
            [row for row in validation if str(row.get("market") or "unknown") == market],
            slope=slope,
            intercept=intercept,
        )
        for market in markets
    }


def _split_forward_holdout(
    samples: list[dict[str, Any]], target_validation_count: int
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str | None, bool]:
    """Split chronologically without leaking publication batches or games."""
    if len(samples) < 2:
        return [], list(samples), None, False
    target = min(max(1, int(target_validation_count)), len(samples) - 1)
    boundary = len(samples) - target

    boundary_time = samples[boundary]["releasedAt"]
    while boundary > 0 and samples[boundary - 1]["releasedAt"] == boundary_time:
        boundary -= 1

    while boundary > 0:
        validation_games = {
            row["gameId"] for row in samples[boundary:] if row.get("gameId")
        }
        crossing_indexes = [
            idx
            for idx, row in enumerate(samples[:boundary])
            if row.get("gameId") and row["gameId"] in validation_games
        ]
        if not crossing_indexes:
            break
        boundary = min(crossing_indexes)
        boundary_time = samples[boundary]["releasedAt"]
        while boundary > 0 and samples[boundary - 1]["releasedAt"] == boundary_time:
            boundary -= 1

    if boundary <= 0:
        return [], list(samples), samples[0]["releasedAt"] if samples else None, False

    train = samples[:boundary]
    validation = samples[boundary:]
    train_games = {row["gameId"] for row in train if row.get("gameId")}
    validation_games = {
        row["gameId"] for row in validation if row.get("gameId")
    }
    forward_integrity = (
        bool(train)
        and bool(validation)
        and train[-1]["releasedAt"] < validation[0]["releasedAt"]
        and train_games.isdisjoint(validation_games)
    )
    return train, validation, validation[0]["releasedAt"], forward_integrity


def build_candidate_report(
    receipts: Iterable[dict[str, Any]],
    *,
    min_samples: int | None = None,
    min_validation_samples: int | None = None,
    min_market_validation_samples: int | None = None,
    train_fraction: float | None = None,
    min_brier_improvement: float | None = None,
    max_ece_regression: float | None = None,
    max_market_skill_regression: float | None = None,
) -> dict[str, Any]:
    """Build a deterministic, forward-holdout challenger governance report."""
    active = policy()
    min_samples = int(
        active["minGradedSamples"] if min_samples is None else min_samples
    )
    min_validation_samples = int(
        active["minValidationSamples"]
        if min_validation_samples is None
        else min_validation_samples
    )
    min_market_validation_samples = int(
        active["minMarketValidationSamples"]
        if min_market_validation_samples is None
        else min_market_validation_samples
    )
    train_fraction = float(
        active["trainFraction"] if train_fraction is None else train_fraction
    )
    min_brier_improvement = float(
        active["minBrierImprovement"]
        if min_brier_improvement is None
        else min_brier_improvement
    )
    max_ece_regression = float(
        active["maxEceRegression"]
        if max_ece_regression is None
        else max_ece_regression
    )
    max_market_skill_regression = float(
        active["maxMarketSkillRegression"]
        if max_market_skill_regression is None
        else max_market_skill_regression
    )

    receipt_list = list(receipts)
    samples = [
        sample for receipt in receipt_list if (sample := _sample(receipt)) is not None
    ]
    samples.sort(key=lambda row: (row["releasedAt"], row["receiptId"]))
    base = {
        "available": True,
        "model": MODEL_NAME,
        "modelVersion": MODEL_VERSION,
        "autoApply": False,
        "productionApplied": False,
        "receiptCount": len(receipt_list),
        "gradedSamples": len(samples),
        "policy": {
            "minGradedSamples": min_samples,
            "minValidationSamples": min_validation_samples,
            "minMarketValidationSamples": min_market_validation_samples,
            "trainFraction": train_fraction,
            "minBrierImprovement": min_brier_improvement,
            "maxEceRegression": max_ece_regression,
            "maxMarketSkillRegression": max_market_skill_regression,
            "receiptWindowLimit": active["receiptWindowLimit"],
        },
        "safetyContract": {
            "readOnly": True,
            "providerRequests": 0,
            "writesLedger": False,
            "writesTracker": False,
            "changesModelProbabilities": False,
            "changesActionabilityThresholds": False,
            "changesBankrollPolicy": False,
            "automaticPromotion": False,
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
                "checks": {},
            },
        }

    validation_target = max(
        min_validation_samples,
        int(round(len(samples) * (1.0 - train_fraction))),
    )
    validation_target = min(validation_target, len(samples) - 1)
    train, validation, boundary_released_at, forward_integrity = _split_forward_holdout(
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
                "reason": "insufficient_leakage_safe_validation_split",
                "checks": {
                    "forwardHoldoutIntegrity": forward_integrity,
                    "validationSampleFloor": len(validation)
                    >= min_validation_samples,
                },
            },
        }

    slope, intercept, train_challenger = _fit_candidate(train)
    train_champion = _metrics(train)
    validation_champion = _metrics(validation)
    validation_challenger = _metrics(
        validation, slope=slope, intercept=intercept
    )
    brier_improvement = round(
        float(validation_champion["brier"])
        - float(validation_challenger["brier"]),
        6,
    )
    champion_ece = float(validation_champion["ece"] or 0.0)
    challenger_ece = float(validation_challenger["ece"] or 0.0)
    ece_regression = round(challenger_ece - champion_ece, 6)
    champion_market_skill = validation_champion.get("brierSkillVsMarket")
    challenger_market_skill = validation_challenger.get("brierSkillVsMarket")
    market_skill_delta = None
    if isinstance(champion_market_skill, (int, float)) and isinstance(
        challenger_market_skill, (int, float)
    ):
        market_skill_delta = round(
            float(challenger_market_skill) - float(champion_market_skill), 6
        )

    identity = slope == 1.0 and intercept == 0.0
    checks = {
        "nonIdentityCandidate": not identity,
        "forwardHoldoutIntegrity": forward_integrity,
        "validationSampleFloor": len(validation) >= min_validation_samples,
        "brierImprovement": brier_improvement >= min_brier_improvement,
        "eceRegressionBounded": ece_regression <= max_ece_regression,
        "marketBenchmarkSampleFloor": int(
            validation_challenger["marketBenchmarkSamples"] or 0
        )
        >= min_market_validation_samples,
        "marketSkillRegressionBounded": market_skill_delta is not None
        and market_skill_delta >= -max_market_skill_regression,
    }
    eligible = all(checks.values())
    candidate = {
        "candidateId": _candidate_id(train, slope, intercept),
        "family": "logit-affine",
        "parameters": {"slope": slope, "intercept": intercept},
        "train": {
            "champion": train_champion,
            "challenger": train_challenger,
        },
        "validation": {
            "champion": validation_champion,
            "challenger": validation_challenger,
            "perMarketChampion": _market_segments(
                validation, slope=1.0, intercept=0.0
            ),
            "perMarketChallenger": _market_segments(
                validation, slope=slope, intercept=intercept
            ),
        },
        "validationBrierImprovement": brier_improvement,
        "validationEceRegression": ece_regression,
        "validationMarketSkillDelta": market_skill_delta,
        "trainSamples": len(train),
        "validationSamples": len(validation),
        "validationMarketBenchmarkSamples": int(
            validation_challenger["marketBenchmarkSamples"] or 0
        ),
        "validationBoundaryReleasedAt": boundary_released_at,
        "validationIsForwardHoldout": forward_integrity,
        "validationPreventsBatchAndGameLeakage": forward_integrity,
    }
    failed_checks = [key for key, passed in checks.items() if not passed]
    return {
        **base,
        "state": "review" if eligible else "rejected",
        "candidate": candidate,
        "promotionGate": {
            "eligible": eligible,
            "requiresHumanReview": True,
            "automaticApply": False,
            "reason": (
                "candidate_clears_forward_holdout_governance"
                if eligible
                else "candidate_does_not_clear_forward_holdout_governance"
            ),
            "checks": checks,
            "failedChecks": failed_checks,
        },
    }


def build_production_report() -> dict[str, Any]:
    """Read the P4.4 ledger and build the current P4.9 governance report."""
    status = p44.ledger_status()
    if not status.get("available"):
        return {
            "available": False,
            "model": MODEL_NAME,
            "modelVersion": MODEL_VERSION,
            "state": "unavailable",
            "autoApply": False,
            "productionApplied": False,
            "ledger": status,
            "safetyContract": {
                "readOnly": True,
                "providerRequests": 0,
                "writesLedger": False,
                "writesTracker": False,
                "changesModelProbabilities": False,
                "changesActionabilityThresholds": False,
                "changesBankrollPolicy": False,
                "automaticPromotion": False,
            },
        }
    report = build_candidate_report(p44.list_receipts(limit=RECEIPT_LIMIT))
    report["ledger"] = status
    return report
