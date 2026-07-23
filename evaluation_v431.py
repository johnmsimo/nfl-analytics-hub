"""Deterministic model evaluation and champion selection for NFL Analytics Hub v4.3.1."""

from __future__ import annotations

import hashlib
import json
import math
import re
import time
from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import Any

from lifecycle_v43 import normalize_promotion_policy

VERSION = "4.3.1"
REGISTRY_CONTRACT_VERSION = "4.3.0"
MAX_OBSERVATIONS = 50_000
MAX_CHECK_RESULTS = 20
MAX_METADATA_BYTES = 256 * 1024

_IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9._-]{0,79}$")
_MODEL_VERSION_ID = re.compile(r"^mv_[a-f0-9]{20}$")
_POLICY_ID = re.compile(r"^policy_[a-f0-9]{24}$")
_EVALUATION_ID = re.compile(r"^eval_[a-f0-9]{24}$")
_SHA256 = re.compile(r"^(?:sha256:)?([a-f0-9]{64})$")
_CLASSIFICATION_METRICS = {
    "accuracy",
    "brier-score",
    "calibration-error",
    "log-loss",
}
_SUPPORTED_METRICS = {
    "accuracy": {
        "direction": "higher",
        "description": "Binary accuracy at a 0.5 probability threshold.",
    },
    "brier-score": {
        "direction": "lower",
        "description": "Mean squared probability error for binary outcomes.",
    },
    "calibration-error": {
        "direction": "lower",
        "description": "Ten-bin expected calibration error for binary outcomes.",
    },
    "log-loss": {
        "direction": "lower",
        "description": "Clipped binary cross-entropy.",
    },
    "mae": {
        "direction": "lower",
        "description": "Mean absolute prediction error.",
    },
    "rmse": {
        "direction": "lower",
        "description": "Root mean squared prediction error.",
    },
}


def _canonical_json(value: Any, field: str, maximum_bytes: int = MAX_METADATA_BYTES) -> str:
    try:
        raw = json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be JSON-safe and contain only finite numbers") from exc
    if len(raw.encode("utf-8")) > maximum_bytes:
        raise ValueError(f"{field} exceeds {maximum_bytes} bytes")
    return raw


def _digest(value: Any, field: str) -> str:
    raw = _canonical_json(value, field)
    return f"sha256:{hashlib.sha256(raw.encode()).hexdigest()}"


def _identifier(value: Any, field: str) -> str:
    result = str(value or "").strip().lower()
    if not _IDENTIFIER.fullmatch(result):
        raise ValueError(f"{field} must use 1-80 lowercase letters, numbers, dots, dashes, or underscores")
    return result


def _sha256(value: Any, field: str) -> str:
    text = str(value or "").strip().lower()
    match = _SHA256.fullmatch(text)
    if not match:
        raise ValueError(f"{field} must be a SHA-256 digest")
    return f"sha256:{match.group(1)}"


def _timestamp(value: Any, field: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a finite non-negative number") from exc
    if not math.isfinite(result) or result < 0:
        raise ValueError(f"{field} must be a finite non-negative number")
    return round(result, 6)


def _finite_number(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a finite number")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a finite number") from exc
    if not math.isfinite(result):
        raise ValueError(f"{field} must be a finite number")
    return result


def _normalized_model(
    value: Any,
    field: str,
    *,
    required_status: str,
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be a JSON object")
    if value.get("contract_version") != REGISTRY_CONTRACT_VERSION:
        raise ValueError(f"{field} must use contract_version {REGISTRY_CONTRACT_VERSION}")
    model_version_id = str(value.get("model_version_id", "")).strip().lower()
    if not _MODEL_VERSION_ID.fullmatch(model_version_id):
        raise ValueError(f"{field} must contain a normalized model_version_id")
    if value.get("status") != required_status:
        raise ValueError(f"{field} must have status {required_status}")
    model_key = _identifier(value.get("model_key"), f"{field} model_key")
    target = _identifier(value.get("target"), f"{field} target")
    schema_digest = _sha256(
        value.get("feature_schema_digest"),
        f"{field} feature_schema_digest",
    )
    artifact = value.get("artifact")
    if artifact is not None and not isinstance(artifact, Mapping):
        raise ValueError(f"{field} artifact must be a JSON object")
    normalized = deepcopy(dict(value))
    normalized["model_version_id"] = model_version_id
    normalized["model_key"] = model_key
    normalized["target"] = target
    normalized["feature_schema_digest"] = schema_digest
    return normalized


def _policy(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("policy must be a JSON object")
    normalized = normalize_promotion_policy(value)
    supplied_id = value.get("policy_id")
    if supplied_id is not None and str(supplied_id).strip().lower() != normalized["policy_id"]:
        raise ValueError("policy_id does not match normalized policy contents")
    return normalized


def _observations(
    value: Any,
    *,
    champion_required: bool,
    classification_required: bool,
) -> list[dict[str, float]]:
    if isinstance(value, str | bytes) or not isinstance(value, Sequence):
        raise ValueError("observations must be a list")
    if not value or len(value) > MAX_OBSERVATIONS:
        raise ValueError(f"observations must contain between 1 and {MAX_OBSERVATIONS} items")
    rows: list[dict[str, float]] = []
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            raise ValueError(f"observation {index} must be a JSON object")
        actual = _finite_number(item.get("actual"), f"observation {index} actual")
        candidate = _finite_number(
            item.get("candidate_prediction"),
            f"observation {index} candidate_prediction",
        )
        row = {"actual": actual, "candidate_prediction": candidate}
        if champion_required:
            row["champion_prediction"] = _finite_number(
                item.get("champion_prediction"),
                f"observation {index} champion_prediction",
            )
        if classification_required:
            if actual not in {0.0, 1.0}:
                raise ValueError(f"observation {index} actual must be 0 or 1 for classification metrics")
            predictions = [candidate]
            if champion_required:
                predictions.append(row["champion_prediction"])
            if any(prediction < 0 or prediction > 1 for prediction in predictions):
                raise ValueError(
                    f"observation {index} predictions must be between 0 and 1 for classification metrics"
                )
        rows.append(row)
    return rows


def _metric_value(name: str, rows: Sequence[Mapping[str, float]], prediction_key: str) -> float:
    actuals = [row["actual"] for row in rows]
    predictions = [row[prediction_key] for row in rows]
    if name == "mae":
        value = sum(abs(prediction - actual) for prediction, actual in zip(predictions, actuals, strict=True))
        return round(value / len(rows), 10)
    if name == "rmse":
        value = sum(
            (prediction - actual) ** 2 for prediction, actual in zip(predictions, actuals, strict=True)
        )
        return round(math.sqrt(value / len(rows)), 10)
    if name == "brier-score":
        value = sum(
            (prediction - actual) ** 2 for prediction, actual in zip(predictions, actuals, strict=True)
        )
        return round(value / len(rows), 10)
    if name == "log-loss":
        epsilon = 1e-15
        total = 0.0
        for prediction, actual in zip(predictions, actuals, strict=True):
            clipped = min(max(prediction, epsilon), 1 - epsilon)
            total -= actual * math.log(clipped) + (1 - actual) * math.log(1 - clipped)
        return round(total / len(rows), 10)
    if name == "accuracy":
        correct = sum(
            (prediction >= 0.5) == bool(actual)
            for prediction, actual in zip(predictions, actuals, strict=True)
        )
        return round(correct / len(rows), 10)
    if name == "calibration-error":
        buckets: list[list[tuple[float, float]]] = [[] for _ in range(10)]
        for prediction, actual in zip(predictions, actuals, strict=True):
            buckets[min(int(prediction * 10), 9)].append((prediction, actual))
        error = 0.0
        for bucket in buckets:
            if not bucket:
                continue
            average_prediction = sum(item[0] for item in bucket) / len(bucket)
            average_actual = sum(item[1] for item in bucket) / len(bucket)
            error += (len(bucket) / len(rows)) * abs(average_prediction - average_actual)
        return round(error, 10)
    raise ValueError(f"metric {name} is not supported by v4.3.1")


def evaluation_metric_catalog() -> dict[str, Any]:
    return {
        "contract_version": VERSION,
        "metrics": [{"name": name, **details} for name, details in sorted(_SUPPORTED_METRICS.items())],
        "observation_fields": [
            "actual",
            "candidate_prediction",
            "champion_prediction",
        ],
        "maximum_observations": MAX_OBSERVATIONS,
    }


def _integrity_checks(
    payload: Mapping[str, Any],
    policy: Mapping[str, Any],
    challenger: Mapping[str, Any],
    champion: Mapping[str, Any] | None,
) -> list[dict[str, Any]]:
    supplied = payload.get("check_results", {})
    if not isinstance(supplied, Mapping):
        raise ValueError("check_results must be a JSON object")
    if len(supplied) > MAX_CHECK_RESULTS:
        raise ValueError(f"check_results cannot contain more than {MAX_CHECK_RESULTS} items")
    results: list[dict[str, Any]] = []
    for name in policy["required_checks"]:
        if name == "artifact.integrity":
            artifact = challenger.get("artifact")
            expected = artifact.get("digest") if isinstance(artifact, Mapping) else None
            observed_value = payload.get("observed_artifact_digest")
            observed = (
                _sha256(observed_value, "observed_artifact_digest") if observed_value is not None else None
            )
            passed = expected is not None and observed == expected
            results.append(
                {
                    "name": name,
                    "passed": passed,
                    "evidence_digest": observed,
                    "reason": (
                        "observed artifact digest matches registry metadata"
                        if passed
                        else "matching observed artifact digest is required"
                    ),
                }
            )
            continue
        if name == "feature.schema.compatibility":
            expected_value = payload.get("expected_feature_schema_digest")
            expected = (
                _sha256(expected_value, "expected_feature_schema_digest")
                if expected_value is not None
                else None
            )
            challenger_digest = challenger["feature_schema_digest"]
            champion_compatible = champion is None or champion["feature_schema_digest"] == challenger_digest
            passed = expected == challenger_digest and champion_compatible
            results.append(
                {
                    "name": name,
                    "passed": passed,
                    "evidence_digest": expected,
                    "reason": (
                        "challenger schema matches the serving contract and champion"
                        if passed and champion is not None
                        else (
                            "challenger schema matches the serving contract"
                            if passed
                            else "compatible serving and champion schemas are required"
                        )
                    ),
                }
            )
            continue
        item = supplied.get(name)
        if not isinstance(item, Mapping):
            results.append(
                {
                    "name": name,
                    "passed": False,
                    "evidence_digest": None,
                    "reason": "caller-supplied check evidence is required",
                }
            )
            continue
        passed_value = item.get("passed")
        if not isinstance(passed_value, bool):
            raise ValueError(f"check_results {name} passed must be a boolean")
        evidence_value = item.get("evidence_digest")
        evidence = (
            _sha256(evidence_value, f"check_results {name} evidence_digest")
            if evidence_value is not None
            else None
        )
        results.append(
            {
                "name": name,
                "passed": passed_value and evidence is not None,
                "evidence_digest": evidence,
                "reason": (
                    "caller-supplied check passed with evidence"
                    if passed_value and evidence is not None
                    else "passing caller-supplied evidence is required"
                ),
            }
        )
    return results


def run_held_out_evaluation(
    payload: Mapping[str, Any],
    *,
    evaluated_at: float | None = None,
) -> dict[str, Any]:
    """Evaluate one candidate against policy and optional champion on held-out observations."""
    if not isinstance(payload, Mapping):
        raise ValueError("evaluation must be a JSON object")
    policy = _policy(payload.get("policy"))
    challenger = _normalized_model(
        payload.get("challenger"),
        "challenger",
        required_status="candidate",
    )
    champion_value = payload.get("champion")
    champion = (
        None
        if champion_value is None
        else _normalized_model(champion_value, "champion", required_status="champion")
    )
    if challenger["model_key"] != policy["model_key"] or challenger["target"] != policy["target"]:
        raise ValueError("challenger model_key and target must match the policy")
    if champion is not None:
        if champion["model_key"] != policy["model_key"] or champion["target"] != policy["target"]:
            raise ValueError("champion model_key and target must match the policy")
        if champion["model_version_id"] == challenger["model_version_id"]:
            raise ValueError("champion and challenger must be different model versions")
    elif not policy["allow_missing_champion"]:
        raise ValueError("policy does not allow evaluation without a champion")

    metric_names = [item["name"] for item in policy["metrics"]]
    unsupported = sorted(set(metric_names) - _SUPPORTED_METRICS.keys())
    if unsupported:
        raise ValueError(f"unsupported evaluation metrics: {', '.join(unsupported)}")
    incompatible_directions = [
        item["name"]
        for item in policy["metrics"]
        if item["direction"] != _SUPPORTED_METRICS[item["name"]]["direction"]
    ]
    if incompatible_directions:
        raise ValueError(
            "metric direction does not match the v4.3.1 metric catalog: " + ", ".join(incompatible_directions)
        )
    rows = _observations(
        payload.get("observations"),
        champion_required=champion is not None,
        classification_required=bool(set(metric_names) & _CLASSIFICATION_METRICS),
    )
    dataset_digest = _sha256(payload.get("dataset_digest"), "dataset_digest")
    window = payload.get("window")
    if not isinstance(window, Mapping):
        raise ValueError("window must be a JSON object")
    window_started_at = _timestamp(window.get("started_at"), "window started_at")
    window_finished_at = _timestamp(window.get("finished_at"), "window finished_at")
    if window_finished_at < window_started_at:
        raise ValueError("window finished_at cannot precede started_at")
    timestamp = _timestamp(
        time.time() if evaluated_at is None else evaluated_at,
        "evaluated_at",
    )
    if timestamp < window_finished_at:
        raise ValueError("evaluated_at cannot precede window finished_at")

    metrics: list[dict[str, Any]] = []
    for rule in policy["metrics"]:
        name = rule["name"]
        candidate_value = _metric_value(name, rows, "candidate_prediction")
        champion_metric = _metric_value(name, rows, "champion_prediction") if champion is not None else None
        threshold_passed = (
            candidate_value >= rule["threshold"]
            if rule["direction"] == "higher"
            else candidate_value <= rule["threshold"]
        )
        improvement = (
            None
            if champion_metric is None
            else (
                candidate_value - champion_metric
                if rule["direction"] == "higher"
                else champion_metric - candidate_value
            )
        )
        improvement_passed = (
            policy["allow_missing_champion"]
            if improvement is None
            else improvement >= rule["minimum_improvement"]
        )
        metrics.append(
            {
                **rule,
                "candidate_value": candidate_value,
                "champion_value": champion_metric,
                "improvement": None if improvement is None else round(improvement, 10),
                "threshold_passed": threshold_passed,
                "improvement_passed": improvement_passed,
                "passed": threshold_passed and improvement_passed,
            }
        )

    checks = _integrity_checks(payload, policy, challenger, champion)
    sample_gate = len(rows) >= policy["minimum_samples"]
    checks_gate = all(item["passed"] for item in checks)
    metrics_gate = (
        all(item["passed"] for item in metrics)
        if policy["require_all_metrics"]
        else any(item["passed"] for item in metrics)
    )
    body = {
        "contract_version": VERSION,
        "event_type": "model.evaluation.completed",
        "policy_id": policy["policy_id"],
        "policy": policy,
        "challenger_model_version_id": challenger["model_version_id"],
        "champion_model_version_id": (champion["model_version_id"] if champion is not None else None),
        "dataset_digest": dataset_digest,
        "sample_count": len(rows),
        "window": {
            "started_at": window_started_at,
            "finished_at": window_finished_at,
        },
        "evaluated_at": timestamp,
        "metrics": metrics,
        "checks": checks,
        "gates": {
            "minimum_samples": sample_gate,
            "required_checks": checks_gate,
            "policy_metrics": metrics_gate,
        },
        "passed": sample_gate and checks_gate and metrics_gate,
    }
    evidence_digest = _digest(body, "evaluation evidence")
    evaluation_id = hashlib.sha256(evidence_digest.encode()).hexdigest()[:24]
    return {
        **body,
        "evaluation_id": f"eval_{evaluation_id}",
        "evidence_digest": evidence_digest,
    }


def _validated_evaluation(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("evaluation must be a JSON object")
    if value.get("contract_version") != VERSION:
        raise ValueError(f"evaluation must use contract_version {VERSION}")
    evaluation_id = str(value.get("evaluation_id", "")).strip().lower()
    if not _EVALUATION_ID.fullmatch(evaluation_id):
        raise ValueError("evaluation_id must be a normalized v4.3.1 identity")
    evidence_digest = _sha256(value.get("evidence_digest"), "evaluation evidence_digest")
    body = deepcopy(dict(value))
    body.pop("evaluation_id", None)
    body.pop("evidence_digest", None)
    expected_digest = _digest(body, "evaluation evidence")
    expected_id = f"eval_{hashlib.sha256(expected_digest.encode()).hexdigest()[:24]}"
    if evidence_digest != expected_digest or evaluation_id != expected_id:
        raise ValueError("evaluation evidence integrity check failed")
    if body.get("event_type") != "model.evaluation.completed":
        raise ValueError("evaluation event_type is invalid")
    normalized_policy = _policy(body.get("policy"))
    policy_id = normalized_policy["policy_id"]
    if not _POLICY_ID.fullmatch(policy_id) or policy_id != body.get("policy_id"):
        raise ValueError("evaluation policy identity is invalid")
    challenger_id = str(body.get("challenger_model_version_id", "")).strip().lower()
    if not _MODEL_VERSION_ID.fullmatch(challenger_id):
        raise ValueError("evaluation challenger identity is invalid")
    champion_value = body.get("champion_model_version_id")
    champion_id = None if champion_value is None else str(champion_value).strip().lower()
    if champion_id is not None and not _MODEL_VERSION_ID.fullmatch(champion_id):
        raise ValueError("evaluation champion identity is invalid")
    if champion_id == challenger_id:
        raise ValueError("evaluation champion and challenger identities must differ")

    sample_count = body.get("sample_count")
    if (
        isinstance(sample_count, bool)
        or not isinstance(sample_count, int)
        or sample_count < 1
        or sample_count > MAX_OBSERVATIONS
    ):
        raise ValueError("evaluation sample_count is invalid")
    metric_values = body.get("metrics")
    if not isinstance(metric_values, list):
        raise ValueError("evaluation metrics must be a list")
    metric_rules = {item["name"]: item for item in normalized_policy["metrics"]}
    if len(metric_values) != len(metric_rules):
        raise ValueError("evaluation metrics do not match the promotion policy")
    validated_metric_passes: list[bool] = []
    seen_metrics: set[str] = set()
    for item in metric_values:
        if not isinstance(item, Mapping):
            raise ValueError("each evaluation metric must be a JSON object")
        name = str(item.get("name", "")).strip().lower()
        rule = metric_rules.get(name)
        if rule is None or name in seen_metrics:
            raise ValueError("evaluation metrics do not match the promotion policy")
        seen_metrics.add(name)
        if any(item.get(key) != rule[key] for key in ("direction", "threshold", "minimum_improvement")):
            raise ValueError(f"evaluation metric {name} rule does not match the promotion policy")
        candidate_value = _finite_number(
            item.get("candidate_value"),
            f"evaluation metric {name} candidate_value",
        )
        champion_metric_value = item.get("champion_value")
        if champion_id is None:
            if champion_metric_value is not None:
                raise ValueError(f"evaluation metric {name} cannot contain a champion value")
            normalized_champion_value = None
        else:
            normalized_champion_value = _finite_number(
                champion_metric_value,
                f"evaluation metric {name} champion_value",
            )
        threshold_passed = (
            candidate_value >= rule["threshold"]
            if rule["direction"] == "higher"
            else candidate_value <= rule["threshold"]
        )
        improvement = (
            None
            if normalized_champion_value is None
            else (
                candidate_value - normalized_champion_value
                if rule["direction"] == "higher"
                else normalized_champion_value - candidate_value
            )
        )
        expected_improvement = None if improvement is None else round(improvement, 10)
        if item.get("improvement") != expected_improvement:
            raise ValueError(f"evaluation metric {name} improvement is inconsistent")
        improvement_passed = (
            normalized_policy["allow_missing_champion"]
            if improvement is None
            else improvement >= rule["minimum_improvement"]
        )
        passed = threshold_passed and improvement_passed
        if (
            item.get("threshold_passed") is not threshold_passed
            or item.get("improvement_passed") is not improvement_passed
            or item.get("passed") is not passed
        ):
            raise ValueError(f"evaluation metric {name} gates are inconsistent")
        validated_metric_passes.append(passed)

    check_values = body.get("checks")
    if not isinstance(check_values, list):
        raise ValueError("evaluation checks must be a list")
    required_checks = set(normalized_policy["required_checks"])
    if len(check_values) != len(required_checks):
        raise ValueError("evaluation checks do not match the promotion policy")
    validated_check_passes: list[bool] = []
    seen_checks: set[str] = set()
    for item in check_values:
        if not isinstance(item, Mapping):
            raise ValueError("each evaluation check must be a JSON object")
        name = str(item.get("name", "")).strip().lower()
        if name not in required_checks or name in seen_checks:
            raise ValueError("evaluation checks do not match the promotion policy")
        seen_checks.add(name)
        passed = item.get("passed")
        if not isinstance(passed, bool):
            raise ValueError(f"evaluation check {name} passed must be a boolean")
        evidence = item.get("evidence_digest")
        if evidence is not None:
            _sha256(evidence, f"evaluation check {name} evidence_digest")
        if passed and evidence is None:
            raise ValueError(f"evaluation check {name} passed without evidence")
        validated_check_passes.append(passed)

    expected_gates = {
        "minimum_samples": sample_count >= normalized_policy["minimum_samples"],
        "required_checks": all(validated_check_passes),
        "policy_metrics": (
            all(validated_metric_passes)
            if normalized_policy["require_all_metrics"]
            else any(validated_metric_passes)
        ),
    }
    if body.get("gates") != expected_gates:
        raise ValueError("evaluation gates are inconsistent with policy evidence")
    if body.get("passed") is not all(expected_gates.values()):
        raise ValueError("evaluation outcome is inconsistent with policy gates")
    validated = deepcopy(dict(value))
    validated["policy"] = normalized_policy
    validated["evaluation_id"] = evaluation_id
    validated["evidence_digest"] = evidence_digest
    return validated


def select_champion_challenger(
    payload: Mapping[str, Any],
    *,
    decided_at: float | None = None,
) -> dict[str, Any]:
    """Create an auditable selection and lifecycle-compatible promotion decision."""
    if not isinstance(payload, Mapping):
        raise ValueError("selection must be a JSON object")
    evaluation = _validated_evaluation(payload.get("evaluation"))
    timestamp = _timestamp(
        time.time() if decided_at is None else decided_at,
        "decided_at",
    )
    evaluated_at = _timestamp(evaluation.get("evaluated_at"), "evaluation evaluated_at")
    if timestamp < evaluated_at:
        raise ValueError("decided_at cannot precede evaluated_at")
    policy = evaluation["policy"]
    maximum_age = int(policy["maximum_evaluation_age_seconds"])
    fresh = timestamp - evaluated_at <= maximum_age
    evaluation_passed = evaluation.get("passed") is True
    promote = evaluation_passed and fresh
    challenger_id = evaluation["challenger_model_version_id"]
    champion_id = evaluation.get("champion_model_version_id")
    if promote:
        action = "promote_challenger"
        selected_id = challenger_id
        reasons = ["evaluation passed all policy gates", "evaluation evidence is fresh"]
    elif champion_id is not None:
        action = "retain_champion"
        selected_id = champion_id
        reasons = [
            (
                "evaluation failed one or more policy gates"
                if not evaluation_passed
                else "evaluation evidence is stale"
            )
        ]
    else:
        action = "no_selection"
        selected_id = None
        reasons = [
            (
                "evaluation failed one or more policy gates"
                if not evaluation_passed
                else "evaluation evidence is stale"
            )
        ]
    promotion_decision = {
        "policy_id": evaluation["policy_id"],
        "evaluation_id": evaluation["evaluation_id"],
        "evidence_digest": evaluation["evidence_digest"],
        "passed": promote,
        "evaluated_at": evaluated_at,
    }
    body = {
        "contract_version": VERSION,
        "event_type": "model.champion_challenger.selected",
        "evaluation_id": evaluation["evaluation_id"],
        "challenger_model_version_id": challenger_id,
        "champion_model_version_id": champion_id,
        "selected_model_version_id": selected_id,
        "action": action,
        "reasons": reasons,
        "decided_at": timestamp,
        "freshness": {
            "passed": fresh,
            "age_seconds": round(timestamp - evaluated_at, 6),
            "maximum_age_seconds": maximum_age,
        },
        "promotion_decision": promotion_decision,
    }
    selection_digest = _digest(body, "champion challenger selection")
    selection_id = hashlib.sha256(selection_digest.encode()).hexdigest()[:24]
    return {
        **body,
        "selection_id": f"selection_{selection_id}",
        "selection_digest": selection_digest,
    }


def evaluation_manifest() -> dict[str, Any]:
    return {
        "version": VERSION,
        "name": "Automated Evaluation and Champion/Challenger Selection",
        "registry_contract_version": REGISTRY_CONTRACT_VERSION,
        "features": {
            "held_out_evaluation_records": True,
            "allowlisted_metric_suites": True,
            "artifact_integrity_checks": True,
            "feature_schema_compatibility": True,
            "policy_threshold_enforcement": True,
            "champion_challenger_comparison": True,
            "evidence_backed_promotion_decisions": True,
            "automatic_lifecycle_mutation": False,
        },
        "limits": {
            "observations": MAX_OBSERVATIONS,
            "check_results": MAX_CHECK_RESULTS,
            "metadata_bytes": MAX_METADATA_BYTES,
        },
        "next_increment": "v4.3.2 Retraining and Rollout Controls",
    }
