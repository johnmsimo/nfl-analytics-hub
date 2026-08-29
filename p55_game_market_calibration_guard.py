"""P5.5 post-promotion guard for spread and total calibration champions.

P5.4 creates independently promoted spread/total calibration champions. P5.5
closes that safety loop by measuring each active market champion against the
exact pre-promotion P4.1 selected-side probability reconstructed from immutable
P4.4 receipts and P5.4's append-only market promotion registry.

The guard is observational only. It never calls an odds provider, changes a
probability, writes a receipt, promotes/rolls back a champion, changes
actonability/bankroll policy, or places a wager. It may recommend owner rollback
review for one market without affecting the other market.
"""
from __future__ import annotations

import math
import os
from typing import Any, Iterable

import p44_game_decision_ledger as p44
import p54_game_market_calibration as p54

MODEL_NAME = "p5.5-game-market-calibration-champion-guard"
MODEL_VERSION = "p55-market-champion-guard-v1"
RECEIPT_LIMIT = 2000
EVENT_LIMIT = 200


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
    """Return bounded post-promotion market monitoring thresholds."""
    return {
        "minGradedSamplesPerMarket": _env_int("P55_MIN_GRADED_SAMPLES", 20, 10, 1000),
        "minMarketBenchmarkSamples": _env_int(
            "P55_MIN_MARKET_BENCHMARK_SAMPLES", 12, 5, 1000
        ),
        "maxBrierRegressionVsShadow": _env_float(
            "P55_MAX_BRIER_REGRESSION", 0.02, 0.0, 0.15
        ),
        "maxEceRegressionVsShadow": _env_float(
            "P55_MAX_ECE_REGRESSION", 0.03, 0.0, 0.15
        ),
        "maxMarketSkillRegression": _env_float(
            "P55_MAX_MARKET_SKILL_REGRESSION", 0.02, 0.0, 0.15
        ),
        "receiptWindowLimit": RECEIPT_LIMIT,
        "promotionEventLimit": EVENT_LIMIT,
    }


def _bounded_probability(value: Any) -> float | None:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    return max(0.001, min(0.999, float(value)))


def _sigmoid(value: float) -> float:
    if value >= 0:
        z = math.exp(-value)
        return 1.0 / (1.0 + z)
    z = math.exp(value)
    return z / (1.0 + z)


def inverse_calibrate_probability(
    promoted_probability: float, *, slope: float, intercept: float
) -> float | None:
    """Recover the raw P4.1 selected-side probability when it is invertible."""
    if not isinstance(slope, (int, float)) or float(slope) <= 0:
        return None
    q = _bounded_probability(promoted_probability)
    if q is None or q <= 0.5000001:
        # P5.4 protects the already-selected side with a 0.5 floor. A value at
        # that floor is saturated and the raw probability is not uniquely known.
        return None
    logit = math.log(q / (1.0 - q))
    original = _sigmoid((logit - float(intercept)) / float(slope))
    if original < 0.5 - 1e-9:
        return None
    return max(0.5, min(0.999, original))


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


def _metrics(samples: list[dict[str, Any]], probability_key: str) -> dict[str, Any]:
    pairs = [
        (float(row[probability_key]), float(row["outcome"]))
        for row in samples
        if isinstance(row.get(probability_key), (int, float))
    ]
    if not pairs:
        return {
            "samples": 0,
            "brier": None,
            "ece": None,
            "avgProbability": None,
            "hitRate": None,
        }
    brier = sum((probability - outcome) ** 2 for probability, outcome in pairs) / len(pairs)
    return {
        "samples": len(pairs),
        "brier": round(brier, 6),
        "ece": _ece(pairs),
        "avgProbability": round(sum(row[0] for row in pairs) / len(pairs), 6),
        "hitRate": round(sum(row[1] for row in pairs) / len(pairs), 6),
    }


def _promotion_registry(
    events: Iterable[dict[str, Any]], market: str
) -> dict[str, dict[str, Any]]:
    registry: dict[str, dict[str, Any]] = {}
    for event in events:
        if str(event.get("market") or "") != market:
            continue
        if str(event.get("action") or "") != "promote":
            continue
        candidate_id = str(event.get("candidateId") or "")
        params = event.get("parameters") if isinstance(event.get("parameters"), dict) else {}
        slope = params.get("slope")
        intercept = params.get("intercept")
        if (
            candidate_id
            and isinstance(slope, (int, float))
            and float(slope) > 0
            and isinstance(intercept, (int, float))
        ):
            registry[candidate_id] = {
                "candidateId": candidate_id,
                "market": market,
                "slope": float(slope),
                "intercept": float(intercept),
                "approvedAt": event.get("createdAt"),
                "approvedBy": event.get("approvedBy"),
            }
    return registry


def _candidate_from_source_model(source_model_version: Any, market: str) -> str | None:
    value = str(source_model_version or "")
    if "+" not in value:
        return None
    candidate = value.rsplit("+", 1)[-1].strip()
    expected_prefix = "p54-sp-" if market == "spread" else "p54-to-"
    return candidate if candidate.startswith(expected_prefix) else None


def _sample(
    receipt: dict[str, Any],
    market: str,
    registry: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    grade = str(receipt.get("grade") or "").lower()
    if grade not in {"win", "loss"}:
        return None
    release = receipt.get("release") if isinstance(receipt.get("release"), dict) else {}
    if str(release.get("marketKey") or "") != market:
        return None
    candidate_id = _candidate_from_source_model(release.get("sourceModelVersion"), market)
    if not candidate_id or candidate_id not in registry:
        return None
    promoted = _bounded_probability(release.get("modelProbability"))
    if promoted is None:
        return None
    params = registry[candidate_id]
    shadow = inverse_calibrate_probability(
        promoted,
        slope=float(params["slope"]),
        intercept=float(params["intercept"]),
    )
    if shadow is None:
        return None
    market_probability = _bounded_probability(release.get("fairMarketProbability"))
    return {
        "receiptId": str(receipt.get("receiptId") or ""),
        "releasedAt": str(receipt.get("releasedAt") or ""),
        "market": market,
        "candidateId": candidate_id,
        "promotedProbability": promoted,
        "shadowProbability": shadow,
        "marketProbability": market_probability,
        "outcome": 1.0 if grade == "win" else 0.0,
    }


def build_market_guard_report(
    receipts: Iterable[dict[str, Any]],
    events: Iterable[dict[str, Any]],
    market: str,
    champion: dict[str, Any],
) -> dict[str, Any]:
    """Build one market-isolated post-promotion guard report."""
    market = p54._market(market)  # noqa: SLF001 - canonical P5.4 market validation
    active = policy()
    receipt_list = list(receipts)
    event_list = list(events)
    registry = _promotion_registry(event_list, market)
    champion_candidate = str(champion.get("candidateId") or "") if champion.get("applied") else ""
    base = {
        "available": True,
        "model": MODEL_NAME,
        "modelVersion": MODEL_VERSION,
        "market": market,
        "champion": {
            "state": champion.get("state", "baseline"),
            "applied": champion.get("applied") is True,
            "candidateId": champion.get("candidateId"),
            "approvedAt": champion.get("approvedAt"),
        },
        "receiptCount": len(receipt_list),
        "promotionEventCount": len([row for row in event_list if row.get("market") == market]),
        "policy": active,
        "safetyContract": {
            "readOnly": True,
            "marketIsolated": True,
            "providerRequests": 0,
            "writesLedger": False,
            "writesPromotionRegistry": False,
            "changesModelProbabilities": False,
            "changesSelectedSide": False,
            "changesActionabilityThresholds": False,
            "changesBankrollPolicy": False,
            "automaticRollback": False,
            "placesBets": False,
        },
    }
    if not champion.get("applied") or not champion_candidate:
        return {
            **base,
            "state": "baseline",
            "recommendation": "NO_PROMOTED_MARKET_CHAMPION",
            "gradedSamples": 0,
            "monitor": None,
            "rollbackGate": {
                "recommended": False,
                "requiresHumanReview": True,
                "automaticRollback": False,
                "reason": "baseline_market_champion_active",
                "checks": {},
            },
        }

    samples = [
        sample
        for receipt in receipt_list
        if (sample := _sample(receipt, market, registry)) is not None
        and sample["candidateId"] == champion_candidate
    ]
    samples.sort(key=lambda row: (row["releasedAt"], row["receiptId"]))
    promoted = _metrics(samples, "promotedProbability")
    shadow = _metrics(samples, "shadowProbability")
    market_samples = [row for row in samples if row.get("marketProbability") is not None]
    benchmark = _metrics(market_samples, "marketProbability")
    promoted_market = _metrics(market_samples, "promotedProbability")
    shadow_market = _metrics(market_samples, "shadowProbability")

    brier_delta = None
    if isinstance(promoted.get("brier"), (int, float)) and isinstance(shadow.get("brier"), (int, float)):
        brier_delta = round(float(promoted["brier"]) - float(shadow["brier"]), 6)
    ece_delta = None
    if isinstance(promoted.get("ece"), (int, float)) and isinstance(shadow.get("ece"), (int, float)):
        ece_delta = round(float(promoted["ece"]) - float(shadow["ece"]), 6)

    promoted_market_skill = None
    shadow_market_skill = None
    market_skill_delta = None
    if (
        isinstance(benchmark.get("brier"), (int, float))
        and isinstance(promoted_market.get("brier"), (int, float))
        and isinstance(shadow_market.get("brier"), (int, float))
    ):
        promoted_market_skill = round(float(benchmark["brier"]) - float(promoted_market["brier"]), 6)
        shadow_market_skill = round(float(benchmark["brier"]) - float(shadow_market["brier"]), 6)
        market_skill_delta = round(promoted_market_skill - shadow_market_skill, 6)

    sample_floor = len(samples) >= int(active["minGradedSamplesPerMarket"])
    market_sample_floor = len(market_samples) >= int(active["minMarketBenchmarkSamples"])
    checks = {
        "marketIsolation": all(row.get("market") == market for row in samples),
        "gradedSampleFloor": sample_floor,
        "brierRegressionBounded": brier_delta is not None
        and brier_delta <= float(active["maxBrierRegressionVsShadow"]),
        "eceRegressionBounded": ece_delta is not None
        and ece_delta <= float(active["maxEceRegressionVsShadow"]),
        "marketBenchmarkSampleFloor": market_sample_floor,
        "marketSkillRegressionBounded": market_skill_delta is not None
        and market_skill_delta >= -float(active["maxMarketSkillRegression"]),
    }

    if not sample_floor:
        state = "collecting"
        recommendation = "COLLECT_MORE_MARKET_RESULTS"
        rollback_recommended = False
        reason = "insufficient_post_promotion_market_samples"
    else:
        performance_checks = {
            key: value
            for key, value in checks.items()
            if key not in {"gradedSampleFloor", "marketBenchmarkSampleFloor"}
        }
        if not market_sample_floor:
            performance_checks.pop("marketSkillRegressionBounded", None)
        failed = [key for key, passed in performance_checks.items() if not passed]
        rollback_recommended = bool(failed)
        if rollback_recommended:
            state = "rollback-review"
            recommendation = "REVIEW_MARKET_ROLLBACK_TO_BASELINE"
            reason = "promoted_market_champion_regressed_vs_shadow"
        else:
            state = "healthy"
            recommendation = "KEEP_PROMOTED_MARKET_CHAMPION"
            reason = "promoted_market_champion_within_guardrails"

    return {
        **base,
        "state": state,
        "recommendation": recommendation,
        "gradedSamples": len(samples),
        "monitor": {
            "market": market,
            "candidateId": champion_candidate,
            "promoted": promoted,
            "shadowBaseline": shadow,
            "brierDeltaVsShadow": brier_delta,
            "eceDeltaVsShadow": ece_delta,
            "marketBenchmarkSamples": len(market_samples),
            "marketBenchmark": benchmark,
            "promotedBrierSkillVsMarket": promoted_market_skill,
            "shadowBrierSkillVsMarket": shadow_market_skill,
            "marketSkillDeltaVsShadow": market_skill_delta,
            "firstReleasedAt": samples[0]["releasedAt"] if samples else None,
            "lastReleasedAt": samples[-1]["releasedAt"] if samples else None,
        },
        "rollbackGate": {
            "recommended": rollback_recommended,
            "requiresHumanReview": True,
            "automaticRollback": False,
            "reason": reason,
            "checks": checks,
            "failedChecks": [key for key, passed in checks.items() if not passed],
        },
    }


def build_guard_report(
    receipts: Iterable[dict[str, Any]],
    events: Iterable[dict[str, Any]],
    champions: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Build the aggregate P5.5 spread/total champion guard."""
    receipt_list = list(receipts)
    event_list = list(events)
    markets = {
        market: build_market_guard_report(
            receipt_list,
            event_list,
            market,
            champions.get(market) or {"state": "baseline", "applied": False},
        )
        for market in p54.MARKETS
    }
    states = {report.get("state") for report in markets.values()}
    if "rollback-review" in states:
        state = "rollback-review"
        recommendation = "REVIEW_MARKET_ROLLBACKS"
    elif "collecting" in states:
        state = "collecting"
        recommendation = "COLLECT_MORE_MARKET_RESULTS"
    elif "healthy" in states:
        state = "healthy"
        recommendation = "KEEP_HEALTHY_MARKET_CHAMPIONS"
    else:
        state = "baseline"
        recommendation = "NO_PROMOTED_MARKET_CHAMPIONS"
    rollback_markets = [
        market
        for market, report in markets.items()
        if (report.get("rollbackGate") or {}).get("recommended") is True
    ]
    return {
        "available": True,
        "model": MODEL_NAME,
        "modelVersion": MODEL_VERSION,
        "state": state,
        "recommendation": recommendation,
        "markets": markets,
        "rollbackReviewMarkets": rollback_markets,
        "safetyContract": {
            "readOnly": True,
            "marketIsolated": True,
            "providerRequests": 0,
            "writesLedger": False,
            "writesPromotionRegistry": False,
            "changesModelProbabilities": False,
            "changesSelectedSide": False,
            "changesActionabilityThresholds": False,
            "changesBankrollPolicy": False,
            "automaticRollback": False,
            "placesBets": False,
        },
    }


def build_production_report() -> dict[str, Any]:
    """Read immutable receipts/P5.4 history and evaluate both market champions."""
    receipts = p44.list_receipts(limit=RECEIPT_LIMIT)
    events = p54.list_events(limit=EVENT_LIMIT)
    champions = {market: p54.current_champion(market) for market in p54.MARKETS}
    report = build_guard_report(receipts, events, champions)
    report["ledger"] = p44.ledger_status()
    report["receiptCount"] = len(receipts)
    report["promotionEventCount"] = len(events)
    return report
