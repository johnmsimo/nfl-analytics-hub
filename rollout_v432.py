"""Retraining triggers and rollout controls for NFL Analytics Hub v4.3.2."""

from __future__ import annotations

import hashlib
import json
import math
import re
import time
from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import Any

from distributed_v42 import normalize_job
from evaluation_v431 import select_champion_challenger

VERSION = "4.3.2"
REGISTRY_CONTRACT_VERSION = "4.3.0"
EVALUATION_CONTRACT_VERSION = "4.3.1"
JOB_CONTRACT_VERSION = "4.2.0"
MAX_TRIGGER_SIGNALS = 20
MAX_HEALTH_GATES = 20
MAX_ROLLOUT_STEPS = 10
MAX_METADATA_BYTES = 256 * 1024

_IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9._-]{0,79}$")
_VERSION = re.compile(r"^[a-z0-9][a-z0-9._+-]{0,63}$")
_MODEL_VERSION_ID = re.compile(r"^mv_[a-f0-9]{20}$")
_TRIGGER_ID = re.compile(r"^trigger_[a-f0-9]{24}$")
_SHA256 = re.compile(r"^(?:sha256:)?([a-f0-9]{64})$")
_TRIGGER_KINDS = {
    "data-freshness",
    "feature-drift",
    "performance-degradation",
    "prediction-drift",
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


def _version(value: Any, field: str) -> str:
    result = str(value or "").strip().lower()
    if not _VERSION.fullmatch(result):
        raise ValueError(
            f"{field} must use 1-64 lowercase letters, numbers, dots, dashes, pluses, or underscores"
        )
    return result


def _text(value: Any, field: str, maximum: int) -> str:
    result = str(value or "").strip()
    if not result:
        raise ValueError(f"{field} is required")
    if len(result) > maximum:
        raise ValueError(f"{field} cannot exceed {maximum} characters")
    return result


def _timestamp(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a finite non-negative number")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a finite non-negative number") from exc
    if not math.isfinite(result) or result < 0:
        raise ValueError(f"{field} must be a finite non-negative number")
    return round(result, 6)


def _number(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a finite number")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a finite number") from exc
    if not math.isfinite(result):
        raise ValueError(f"{field} must be a finite number")
    return round(result, 10)


def _integer(value: Any, field: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be an integer")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be an integer") from exc
    if result < minimum or result > maximum:
        raise ValueError(f"{field} must be between {minimum} and {maximum}")
    return result


def _boolean(value: Any, field: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{field} must be a boolean")
    return value


def _sha256(value: Any, field: str) -> str:
    text = str(value or "").strip().lower()
    match = _SHA256.fullmatch(text)
    if not match:
        raise ValueError(f"{field} must be a SHA-256 digest")
    return f"sha256:{match.group(1)}"


def _sequence(value: Any, field: str, maximum: int) -> list[Any]:
    if isinstance(value, str | bytes) or not isinstance(value, Sequence):
        raise ValueError(f"{field} must be a list")
    if not value or len(value) > maximum:
        raise ValueError(f"{field} must contain between 1 and {maximum} items")
    return list(value)


def _model_version(
    value: Any,
    field: str,
    *,
    required_status: str,
    require_artifact: bool = False,
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be a JSON object")
    if value.get("contract_version") != REGISTRY_CONTRACT_VERSION:
        raise ValueError(f"{field} must use contract_version {REGISTRY_CONTRACT_VERSION}")
    identity = str(value.get("model_version_id", "")).strip().lower()
    if not _MODEL_VERSION_ID.fullmatch(identity):
        raise ValueError(f"{field} must contain a normalized model_version_id")
    if value.get("status") != required_status:
        raise ValueError(f"{field} must have status {required_status}")
    normalized = deepcopy(dict(value))
    normalized["model_version_id"] = identity
    normalized["model_key"] = _identifier(value.get("model_key"), f"{field} model_key")
    normalized["target"] = _identifier(value.get("target"), f"{field} target")
    normalized["feature_schema_digest"] = _sha256(
        value.get("feature_schema_digest"),
        f"{field} feature_schema_digest",
    )
    artifact = value.get("artifact")
    if require_artifact and not isinstance(artifact, Mapping):
        raise ValueError(f"{field} must contain artifact metadata")
    if isinstance(artifact, Mapping):
        normalized["artifact"] = {
            **deepcopy(dict(artifact)),
            "digest": _sha256(artifact.get("digest"), f"{field} artifact digest"),
        }
    return normalized


def normalize_retraining_policy(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize bounded, evidence-driven retraining trigger rules."""
    if not isinstance(payload, Mapping):
        raise ValueError("retraining policy must be a JSON object")
    signal_items = _sequence(payload.get("signals"), "signals", MAX_TRIGGER_SIGNALS)
    signals: list[dict[str, Any]] = []
    names: set[str] = set()
    for item in signal_items:
        if not isinstance(item, Mapping):
            raise ValueError("each retraining policy signal must be a JSON object")
        name = _identifier(item.get("name"), "signal name")
        if name in names:
            raise ValueError(f"signals contains duplicate signal {name}")
        names.add(name)
        kind = str(item.get("kind", "")).strip().lower()
        if kind not in _TRIGGER_KINDS:
            raise ValueError(f"signal {name} has an unsupported kind")
        direction = str(item.get("direction", "higher")).strip().lower()
        if direction not in {"higher", "lower"}:
            raise ValueError(f"signal {name} direction must be higher or lower")
        signals.append(
            {
                "name": name,
                "kind": kind,
                "direction": direction,
                "threshold": _number(item.get("threshold"), f"signal {name} threshold"),
                "minimum_samples": _integer(
                    item.get("minimum_samples", 1),
                    f"signal {name} minimum_samples",
                    1,
                    10_000_000,
                ),
            }
        )
    signals.sort(key=lambda item: item["name"])
    body = {
        "model_key": _identifier(payload.get("model_key"), "model_key"),
        "signals": signals,
        "require_all_signals": _boolean(
            payload.get("require_all_signals", False),
            "require_all_signals",
        ),
        "maximum_signal_age_seconds": _integer(
            payload.get("maximum_signal_age_seconds", 24 * 60 * 60),
            "maximum_signal_age_seconds",
            60,
            30 * 24 * 60 * 60,
        ),
        "cooldown_seconds": _integer(
            payload.get("cooldown_seconds", 24 * 60 * 60),
            "cooldown_seconds",
            0,
            90 * 24 * 60 * 60,
        ),
    }
    policy_digest = _digest(body, "retraining policy")
    return {
        "contract_version": VERSION,
        "policy_id": f"retrain_policy_{hashlib.sha256(policy_digest.encode()).hexdigest()[:24]}",
        **body,
    }


def _policy(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("policy must be a JSON object")
    normalized = normalize_retraining_policy(value)
    supplied_id = value.get("policy_id")
    if supplied_id is not None and str(supplied_id).strip().lower() != normalized["policy_id"]:
        raise ValueError("policy_id does not match normalized retraining policy")
    return normalized


def evaluate_retraining_triggers(
    payload: Mapping[str, Any],
    *,
    evaluated_at: float | None = None,
) -> dict[str, Any]:
    """Evaluate caller-supplied drift and performance signals without inventing measurements."""
    if not isinstance(payload, Mapping):
        raise ValueError("trigger evaluation must be a JSON object")
    policy = _policy(payload.get("policy"))
    model = _model_version(
        payload.get("model_version"),
        "model_version",
        required_status="champion",
    )
    if model["model_key"] != policy["model_key"]:
        raise ValueError("policy model_key does not match model_version")
    timestamp = _timestamp(
        time.time() if evaluated_at is None else evaluated_at,
        "evaluated_at",
    )
    supplied = _sequence(payload.get("signals"), "signals", MAX_TRIGGER_SIGNALS)
    supplied_by_name: dict[str, Mapping[str, Any]] = {}
    for item in supplied:
        if not isinstance(item, Mapping):
            raise ValueError("each observed signal must be a JSON object")
        name = _identifier(item.get("name"), "observed signal name")
        if name in supplied_by_name:
            raise ValueError(f"observed signals contains duplicate signal {name}")
        supplied_by_name[name] = item
    expected_names = {item["name"] for item in policy["signals"]}
    if set(supplied_by_name) != expected_names:
        raise ValueError("observed signals must match the retraining policy signals exactly")

    results: list[dict[str, Any]] = []
    for rule in policy["signals"]:
        observed = supplied_by_name[rule["name"]]
        value = _number(observed.get("value"), f"signal {rule['name']} value")
        observed_at = _timestamp(
            observed.get("observed_at"),
            f"signal {rule['name']} observed_at",
        )
        if observed_at > timestamp:
            raise ValueError(f"signal {rule['name']} observed_at cannot follow evaluated_at")
        sample_count = _integer(
            observed.get("sample_count"),
            f"signal {rule['name']} sample_count",
            1,
            10_000_000,
        )
        evidence_digest = _sha256(
            observed.get("evidence_digest"),
            f"signal {rule['name']} evidence_digest",
        )
        threshold_breached = (
            value >= rule["threshold"] if rule["direction"] == "higher" else value <= rule["threshold"]
        )
        fresh = timestamp - observed_at <= policy["maximum_signal_age_seconds"]
        enough_samples = sample_count >= rule["minimum_samples"]
        results.append(
            {
                **rule,
                "value": value,
                "observed_at": observed_at,
                "sample_count": sample_count,
                "evidence_digest": evidence_digest,
                "fresh": fresh,
                "enough_samples": enough_samples,
                "threshold_breached": threshold_breached,
                "triggered": threshold_breached and fresh and enough_samples,
            }
        )

    last_requested_at = payload.get("last_requested_at")
    if last_requested_at is None:
        cooldown = {"passed": True, "last_requested_at": None, "remaining_seconds": 0.0}
    else:
        last = _timestamp(last_requested_at, "last_requested_at")
        if last > timestamp:
            raise ValueError("last_requested_at cannot follow evaluated_at")
        elapsed = timestamp - last
        remaining = max(0.0, policy["cooldown_seconds"] - elapsed)
        cooldown = {
            "passed": remaining == 0,
            "last_requested_at": last,
            "remaining_seconds": round(remaining, 6),
        }
    signal_outcomes = [item["triggered"] for item in results]
    signal_gate = all(signal_outcomes) if policy["require_all_signals"] else any(signal_outcomes)
    triggered = signal_gate and cooldown["passed"]
    body = {
        "contract_version": VERSION,
        "event_type": "model.retraining.trigger_evaluated",
        "policy_id": policy["policy_id"],
        "policy": policy,
        "model_version_id": model["model_version_id"],
        "model_key": model["model_key"],
        "signals": results,
        "gates": {
            "signal_policy": signal_gate,
            "cooldown": cooldown["passed"],
        },
        "cooldown": cooldown,
        "triggered": triggered,
        "evaluated_at": timestamp,
    }
    evidence_digest = _digest(body, "retraining trigger evaluation")
    trigger_id = hashlib.sha256(evidence_digest.encode()).hexdigest()[:24]
    return {
        **body,
        "trigger_id": f"trigger_{trigger_id}",
        "evidence_digest": evidence_digest,
    }


def _validated_trigger(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("trigger must be a JSON object")
    if value.get("contract_version") != VERSION:
        raise ValueError(f"trigger must use contract_version {VERSION}")
    trigger_id = str(value.get("trigger_id", "")).strip().lower()
    if not _TRIGGER_ID.fullmatch(trigger_id):
        raise ValueError("trigger must contain a normalized trigger_id")
    evidence_digest = _sha256(value.get("evidence_digest"), "trigger evidence_digest")
    body = {
        key: deepcopy(item) for key, item in value.items() if key not in {"trigger_id", "evidence_digest"}
    }
    expected_digest = _digest(body, "retraining trigger evaluation")
    expected_id = f"trigger_{hashlib.sha256(expected_digest.encode()).hexdigest()[:24]}"
    if evidence_digest != expected_digest or trigger_id != expected_id:
        raise ValueError("trigger integrity validation failed")
    policy = _policy(body.get("policy"))
    if body.get("policy_id") != policy["policy_id"]:
        raise ValueError("trigger policy evidence is inconsistent")
    signal_results = body.get("signals")
    if not isinstance(signal_results, list) or not signal_results:
        raise ValueError("trigger signals must be a non-empty list")
    evaluated_at = _timestamp(body.get("evaluated_at"), "trigger evaluated_at")
    rules = {item["name"]: item for item in policy["signals"]}
    outcomes: list[bool] = []
    seen: set[str] = set()
    for item in signal_results:
        if not isinstance(item, Mapping):
            raise ValueError("trigger signal evidence is inconsistent")
        name = _identifier(item.get("name"), "trigger signal name")
        rule = rules.get(name)
        if rule is None or name in seen:
            raise ValueError("trigger signal evidence is inconsistent")
        seen.add(name)
        for field in ("kind", "direction", "threshold", "minimum_samples"):
            if item.get(field) != rule[field]:
                raise ValueError("trigger signal evidence is inconsistent")
        signal_value = _number(item.get("value"), f"trigger signal {name} value")
        observed_at = _timestamp(
            item.get("observed_at"),
            f"trigger signal {name} observed_at",
        )
        sample_count = _integer(
            item.get("sample_count"),
            f"trigger signal {name} sample_count",
            1,
            10_000_000,
        )
        _sha256(item.get("evidence_digest"), f"trigger signal {name} evidence_digest")
        expected_threshold = (
            signal_value >= rule["threshold"]
            if rule["direction"] == "higher"
            else signal_value <= rule["threshold"]
        )
        expected_fresh = (
            observed_at <= evaluated_at and evaluated_at - observed_at <= policy["maximum_signal_age_seconds"]
        )
        expected_samples = sample_count >= rule["minimum_samples"]
        expected_triggered = expected_threshold and expected_fresh and expected_samples
        if (
            item.get("threshold_breached") is not expected_threshold
            or item.get("fresh") is not expected_fresh
            or item.get("enough_samples") is not expected_samples
            or item.get("triggered") is not expected_triggered
        ):
            raise ValueError("trigger signal evidence is inconsistent")
        outcomes.append(expected_triggered)
    if seen != set(rules):
        raise ValueError("trigger signal evidence is inconsistent")
    expected_gate = all(outcomes) if policy["require_all_signals"] else any(outcomes)
    gates = body.get("gates")
    cooldown = body.get("cooldown")
    if not isinstance(gates, Mapping) or not isinstance(cooldown, Mapping):
        raise ValueError("trigger gate evidence is missing")
    last_requested_at = cooldown.get("last_requested_at")
    if last_requested_at is None:
        expected_cooldown_passed = True
        expected_remaining = 0.0
    else:
        last = _timestamp(last_requested_at, "trigger cooldown last_requested_at")
        if last > evaluated_at:
            raise ValueError("trigger cooldown evidence is inconsistent")
        expected_remaining = round(
            max(0.0, policy["cooldown_seconds"] - (evaluated_at - last)),
            6,
        )
        expected_cooldown_passed = expected_remaining == 0
    if (
        cooldown.get("passed") is not expected_cooldown_passed
        or cooldown.get("remaining_seconds") != expected_remaining
    ):
        raise ValueError("trigger cooldown evidence is inconsistent")
    if gates.get("signal_policy") is not expected_gate:
        raise ValueError("trigger signal gate is inconsistent")
    if gates.get("cooldown") is not expected_cooldown_passed:
        raise ValueError("trigger cooldown gate is inconsistent")
    if body.get("triggered") is not (expected_gate and expected_cooldown_passed):
        raise ValueError("trigger outcome is inconsistent")
    normalized = deepcopy(dict(value))
    normalized["trigger_id"] = trigger_id
    normalized["evidence_digest"] = evidence_digest
    return normalized


def normalize_retraining_job_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the allowlisted payload consumed by the distributed request handler."""
    if not isinstance(payload, Mapping):
        raise ValueError("retraining payload must be a JSON object")
    if payload.get("request_contract_version") != VERSION:
        raise ValueError(f"retraining payload must use request_contract_version {VERSION}")
    parameters = payload.get("parameters", {})
    if not isinstance(parameters, Mapping):
        raise ValueError("parameters must be a JSON object")
    _canonical_json(parameters, "parameters", 64 * 1024)
    trigger_id = str(payload.get("trigger_id", "")).strip().lower()
    if not _TRIGGER_ID.fullmatch(trigger_id):
        raise ValueError("trigger_id must be a normalized v4.3.2 trigger identity")
    model_version_id = str(payload.get("source_model_version_id", "")).strip().lower()
    if not _MODEL_VERSION_ID.fullmatch(model_version_id):
        raise ValueError("source_model_version_id must be a normalized model identity")
    return {
        "request_contract_version": VERSION,
        "trigger_id": trigger_id,
        "trigger_evidence_digest": _sha256(
            payload.get("trigger_evidence_digest"),
            "trigger_evidence_digest",
        ),
        "source_model_version_id": model_version_id,
        "model_key": _identifier(payload.get("model_key"), "model_key"),
        "target": _identifier(payload.get("target"), "target"),
        "requested_version": _version(payload.get("requested_version"), "requested_version"),
        "dataset_digest": _sha256(payload.get("dataset_digest"), "dataset_digest"),
        "code_version": _text(payload.get("code_version"), "code_version", 120),
        "parameters": deepcopy(dict(parameters)),
        "output_artifact_uri": _text(
            payload.get("output_artifact_uri"),
            "output_artifact_uri",
            500,
        ),
        "requested_by": _text(payload.get("requested_by"), "requested_by", 120),
    }


def build_retraining_request(
    payload: Mapping[str, Any],
    *,
    requested_at: float | None = None,
) -> dict[str, Any]:
    """Build an idempotent v4.2 distributed job for a passing retraining trigger."""
    if not isinstance(payload, Mapping):
        raise ValueError("retraining request must be a JSON object")
    trigger = _validated_trigger(payload.get("trigger"))
    if trigger.get("triggered") is not True:
        raise ValueError("retraining request requires a passing trigger")
    model = _model_version(
        payload.get("model_version"),
        "model_version",
        required_status="champion",
    )
    if model["model_version_id"] != trigger["model_version_id"]:
        raise ValueError("trigger model_version_id does not match model_version")
    timestamp = _timestamp(
        time.time() if requested_at is None else requested_at,
        "requested_at",
    )
    if timestamp < _timestamp(trigger.get("evaluated_at"), "trigger evaluated_at"):
        raise ValueError("requested_at cannot precede trigger evaluated_at")
    job_payload = normalize_retraining_job_payload(
        {
            "request_contract_version": VERSION,
            "trigger_id": trigger["trigger_id"],
            "trigger_evidence_digest": trigger["evidence_digest"],
            "source_model_version_id": model["model_version_id"],
            "model_key": model["model_key"],
            "target": model["target"],
            "requested_version": payload.get("requested_version"),
            "dataset_digest": payload.get("dataset_digest"),
            "code_version": payload.get("code_version"),
            "parameters": payload.get("parameters", {}),
            "output_artifact_uri": payload.get("output_artifact_uri"),
            "requested_by": payload.get("requested_by"),
        }
    )
    job = normalize_job(
        {
            "job_type": "model.retraining.request",
            "namespace": "model-lifecycle",
            "idempotency_key": f"{trigger['trigger_id']}:{job_payload['requested_version']}",
            "priority": payload.get("priority", 6),
            "max_attempts": payload.get("max_attempts", 3),
            "payload": job_payload,
        },
        now=timestamp,
    )
    body = {
        "contract_version": VERSION,
        "event_type": "model.retraining.requested",
        "trigger_id": trigger["trigger_id"],
        "trigger_evidence_digest": trigger["evidence_digest"],
        "source_model_version_id": model["model_version_id"],
        "requested_version": job_payload["requested_version"],
        "requested_at": timestamp,
        "job": job,
    }
    request_digest = _digest(body, "retraining request")
    return {
        **body,
        "request_id": f"retrain_{hashlib.sha256(request_digest.encode()).hexdigest()[:24]}",
        "request_digest": request_digest,
    }


def _health_gates(value: Any) -> list[dict[str, Any]]:
    items = _sequence(value, "health_gates", MAX_HEALTH_GATES)
    gates: list[dict[str, Any]] = []
    names: set[str] = set()
    for item in items:
        if not isinstance(item, Mapping):
            raise ValueError("each health gate must be a JSON object")
        name = _identifier(item.get("name"), "health gate name")
        if name in names:
            raise ValueError(f"health_gates contains duplicate gate {name}")
        names.add(name)
        direction = str(item.get("direction", "")).strip().lower()
        if direction not in {"higher", "lower"}:
            raise ValueError(f"health gate {name} direction must be higher or lower")
        breach_action = str(item.get("breach_action", "rollback")).strip().lower()
        if breach_action not in {"hold", "rollback"}:
            raise ValueError(f"health gate {name} breach_action must be hold or rollback")
        gates.append(
            {
                "name": name,
                "direction": direction,
                "threshold": _number(item.get("threshold"), f"health gate {name} threshold"),
                "minimum_samples": _integer(
                    item.get("minimum_samples", 1),
                    f"health gate {name} minimum_samples",
                    1,
                    10_000_000,
                ),
                "maximum_age_seconds": _integer(
                    item.get("maximum_age_seconds", 15 * 60),
                    f"health gate {name} maximum_age_seconds",
                    30,
                    24 * 60 * 60,
                ),
                "breach_action": breach_action,
            }
        )
    return sorted(gates, key=lambda item: item["name"])


def _rollout_steps(value: Any, mode: str) -> list[dict[str, Any]]:
    items = _sequence(value, "steps", MAX_ROLLOUT_STEPS)
    steps: list[dict[str, Any]] = []
    names: set[str] = set()
    previous_candidate = -1
    previous_shadow = -1
    for item in items:
        if not isinstance(item, Mapping):
            raise ValueError("each rollout step must be a JSON object")
        name = _identifier(item.get("name"), "rollout step name")
        if name in names:
            raise ValueError(f"steps contains duplicate step {name}")
        names.add(name)
        candidate = _integer(
            item.get("candidate_traffic_percent", 0),
            f"step {name} candidate_traffic_percent",
            0,
            100,
        )
        shadow = _integer(
            item.get("shadow_traffic_percent", 0),
            f"step {name} shadow_traffic_percent",
            0,
            100,
        )
        if mode == "shadow":
            if candidate != 0 or shadow <= previous_shadow:
                raise ValueError("shadow steps require zero candidate traffic and increasing shadow traffic")
        elif shadow != 0 or candidate <= previous_candidate:
            raise ValueError("canary steps require zero shadow traffic and increasing candidate traffic")
        previous_candidate = candidate
        previous_shadow = shadow
        steps.append(
            {
                "index": len(steps),
                "name": name,
                "candidate_traffic_percent": candidate,
                "shadow_traffic_percent": shadow,
                "minimum_observation_seconds": _integer(
                    item.get("minimum_observation_seconds", 15 * 60),
                    f"step {name} minimum_observation_seconds",
                    60,
                    7 * 24 * 60 * 60,
                ),
            }
        )
    if mode == "canary" and steps[-1]["candidate_traffic_percent"] != 100:
        raise ValueError("canary rollout must end at 100 percent candidate traffic")
    return steps


def normalize_rollout_plan(
    payload: Mapping[str, Any],
    *,
    planned_at: float | None = None,
) -> dict[str, Any]:
    """Create an evidence-bound shadow or canary plan with an explicit rollback target."""
    if not isinstance(payload, Mapping):
        raise ValueError("rollout plan must be a JSON object")
    candidate = _model_version(
        payload.get("candidate"),
        "candidate",
        required_status="candidate",
        require_artifact=True,
    )
    champion = _model_version(
        payload.get("champion"),
        "champion",
        required_status="champion",
        require_artifact=True,
    )
    if candidate["model_key"] != champion["model_key"] or candidate["target"] != champion["target"]:
        raise ValueError("candidate and champion must share model_key and target")
    evaluation = payload.get("evaluation")
    if not isinstance(evaluation, Mapping):
        raise ValueError("evaluation must be a JSON object")
    selection = select_champion_challenger(
        {"evaluation": evaluation},
        decided_at=payload.get("selection_decided_at"),
    )
    if (
        selection["action"] != "promote_challenger"
        or selection["selected_model_version_id"] != candidate["model_version_id"]
        or selection["champion_model_version_id"] != champion["model_version_id"]
    ):
        raise ValueError("rollout requires a passing champion/challenger selection")
    mode = str(payload.get("mode", "")).strip().lower()
    if mode not in {"canary", "shadow"}:
        raise ValueError("mode must be shadow or canary")
    steps = _rollout_steps(payload.get("steps"), mode)
    health_gates = _health_gates(payload.get("health_gates"))
    timestamp = _timestamp(
        time.time() if planned_at is None else planned_at,
        "planned_at",
    )
    if timestamp < selection["decided_at"]:
        raise ValueError("planned_at cannot precede selection decided_at")
    body = {
        "contract_version": VERSION,
        "event_type": "model.rollout.planned",
        "mode": mode,
        "model_key": candidate["model_key"],
        "target": candidate["target"],
        "candidate_model_version_id": candidate["model_version_id"],
        "champion_model_version_id": champion["model_version_id"],
        "selection_id": selection["selection_id"],
        "selection_digest": selection["selection_digest"],
        "promotion_decision": selection["promotion_decision"],
        "steps": steps,
        "health_gates": health_gates,
        "rollback_target": {
            "model_version_id": champion["model_version_id"],
            "artifact_digest": champion["artifact"]["digest"],
            "feature_schema_digest": champion["feature_schema_digest"],
        },
        "planned_at": timestamp,
        "automatic_traffic_mutation": False,
    }
    plan_digest = _digest(body, "rollout plan")
    return {
        **body,
        "plan_id": f"rollout_{hashlib.sha256(plan_digest.encode()).hexdigest()[:24]}",
        "plan_digest": plan_digest,
    }


def _validated_plan(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("rollout_plan must be a JSON object")
    if value.get("contract_version") != VERSION:
        raise ValueError(f"rollout_plan must use contract_version {VERSION}")
    plan_id = str(value.get("plan_id", "")).strip().lower()
    if not re.fullmatch(r"rollout_[a-f0-9]{24}", plan_id):
        raise ValueError("rollout_plan must contain a normalized plan_id")
    plan_digest = _sha256(value.get("plan_digest"), "rollout plan_digest")
    body = {key: deepcopy(item) for key, item in value.items() if key not in {"plan_id", "plan_digest"}}
    expected_digest = _digest(body, "rollout plan")
    expected_id = f"rollout_{hashlib.sha256(expected_digest.encode()).hexdigest()[:24]}"
    if plan_digest != expected_digest or plan_id != expected_id:
        raise ValueError("rollout plan integrity validation failed")
    if body.get("automatic_traffic_mutation") is not False:
        raise ValueError("rollout plan cannot enable automatic traffic mutation")
    mode = str(body.get("mode", "")).strip().lower()
    if mode not in {"canary", "shadow"}:
        raise ValueError("rollout plan has an unsupported mode")
    if body.get("steps") != _rollout_steps(body.get("steps"), mode):
        raise ValueError("rollout plan steps are inconsistent")
    if body.get("health_gates") != _health_gates(body.get("health_gates")):
        raise ValueError("rollout plan health gates are inconsistent")
    candidate_id = str(body.get("candidate_model_version_id", "")).strip().lower()
    champion_id = str(body.get("champion_model_version_id", "")).strip().lower()
    if not _MODEL_VERSION_ID.fullmatch(candidate_id) or not _MODEL_VERSION_ID.fullmatch(champion_id):
        raise ValueError("rollout plan model identities are invalid")
    promotion = body.get("promotion_decision")
    if not isinstance(promotion, Mapping) or promotion.get("passed") is not True:
        raise ValueError("rollout plan requires passing promotion evidence")
    rollback = body.get("rollback_target")
    if not isinstance(rollback, Mapping) or rollback.get("model_version_id") != champion_id:
        raise ValueError("rollout plan rollback target is inconsistent")
    _sha256(rollback.get("artifact_digest"), "rollout rollback artifact_digest")
    _sha256(
        rollback.get("feature_schema_digest"),
        "rollout rollback feature_schema_digest",
    )
    return deepcopy(dict(value))


def evaluate_rollout_step(
    payload: Mapping[str, Any],
    *,
    evaluated_at: float | None = None,
) -> dict[str, Any]:
    """Evaluate rollout health and emit advance, hold, complete, or rollback intent."""
    if not isinstance(payload, Mapping):
        raise ValueError("rollout step evaluation must be a JSON object")
    plan = _validated_plan(payload.get("rollout_plan"))
    step_index = _integer(
        payload.get("step_index"),
        "step_index",
        0,
        len(plan["steps"]) - 1,
    )
    step = plan["steps"][step_index]
    timestamp = _timestamp(
        time.time() if evaluated_at is None else evaluated_at,
        "evaluated_at",
    )
    step_started_at = _timestamp(payload.get("step_started_at"), "step_started_at")
    if step_started_at < plan["planned_at"]:
        raise ValueError("step_started_at cannot precede planned_at")
    if timestamp < step_started_at:
        raise ValueError("evaluated_at cannot precede step_started_at")
    observations = _sequence(payload.get("health_observations"), "health_observations", MAX_HEALTH_GATES)
    observed_by_name: dict[str, Mapping[str, Any]] = {}
    for item in observations:
        if not isinstance(item, Mapping):
            raise ValueError("each health observation must be a JSON object")
        name = _identifier(item.get("name"), "health observation name")
        if name in observed_by_name:
            raise ValueError(f"health_observations contains duplicate observation {name}")
        observed_by_name[name] = item
    gate_names = {item["name"] for item in plan["health_gates"]}
    if set(observed_by_name) != gate_names:
        raise ValueError("health observations must match the rollout health gates exactly")

    results: list[dict[str, Any]] = []
    for gate in plan["health_gates"]:
        observed = observed_by_name[gate["name"]]
        value = _number(observed.get("value"), f"health observation {gate['name']} value")
        observed_at = _timestamp(
            observed.get("observed_at"),
            f"health observation {gate['name']} observed_at",
        )
        if observed_at > timestamp:
            raise ValueError(f"health observation {gate['name']} cannot follow evaluated_at")
        sample_count = _integer(
            observed.get("sample_count"),
            f"health observation {gate['name']} sample_count",
            1,
            10_000_000,
        )
        evidence_digest = _sha256(
            observed.get("evidence_digest"),
            f"health observation {gate['name']} evidence_digest",
        )
        threshold_passed = (
            value >= gate["threshold"] if gate["direction"] == "higher" else value <= gate["threshold"]
        )
        fresh = timestamp - observed_at <= gate["maximum_age_seconds"]
        enough_samples = sample_count >= gate["minimum_samples"]
        results.append(
            {
                **gate,
                "value": value,
                "observed_at": observed_at,
                "sample_count": sample_count,
                "evidence_digest": evidence_digest,
                "fresh": fresh,
                "enough_samples": enough_samples,
                "threshold_passed": threshold_passed,
                "passed": threshold_passed and fresh and enough_samples,
            }
        )
    duration = round(timestamp - step_started_at, 6)
    duration_passed = duration >= step["minimum_observation_seconds"]
    evidence_failures = [item for item in results if not item["fresh"] or not item["enough_samples"]]
    threshold_failures = [
        item for item in results if item["fresh"] and item["enough_samples"] and not item["threshold_passed"]
    ]
    if evidence_failures:
        action = "hold"
        next_step_index = step_index
        reasons = ["fresh, sample-qualified health evidence is required"]
    elif threshold_failures and any(item["breach_action"] == "rollback" for item in threshold_failures):
        action = "rollback"
        next_step_index = None
        reasons = ["one or more rollback health gates failed"]
    elif threshold_failures:
        action = "hold"
        next_step_index = step_index
        reasons = ["one or more hold health gates failed"]
    elif not duration_passed:
        action = "hold"
        next_step_index = step_index
        reasons = ["minimum observation duration has not elapsed"]
    elif step_index == len(plan["steps"]) - 1:
        action = "complete"
        next_step_index = None
        reasons = ["all rollout steps and health gates passed"]
    else:
        action = "advance"
        next_step_index = step_index + 1
        reasons = ["current rollout step and health gates passed"]
    body = {
        "contract_version": VERSION,
        "event_type": "model.rollout.step_evaluated",
        "plan_id": plan["plan_id"],
        "plan_digest": plan["plan_digest"],
        "step_index": step_index,
        "step": deepcopy(step),
        "health": results,
        "observation_duration_seconds": duration,
        "duration_passed": duration_passed,
        "action": action,
        "next_step_index": next_step_index,
        "rollback_target": deepcopy(plan["rollback_target"]) if action == "rollback" else None,
        "reasons": reasons,
        "evaluated_at": timestamp,
        "automatic_traffic_mutation": False,
    }
    evidence_digest = _digest(body, "rollout step evaluation")
    return {
        **body,
        "decision_id": f"rollout_decision_{hashlib.sha256(evidence_digest.encode()).hexdigest()[:24]}",
        "evidence_digest": evidence_digest,
    }


def rollout_manifest() -> dict[str, Any]:
    return {
        "version": VERSION,
        "name": "Retraining and Rollout Controls",
        "registry_contract_version": REGISTRY_CONTRACT_VERSION,
        "evaluation_contract_version": EVALUATION_CONTRACT_VERSION,
        "job_contract_version": JOB_CONTRACT_VERSION,
        "features": {
            "drift_performance_triggers": True,
            "cooldown_enforcement": True,
            "distributed_retraining_requests": True,
            "typed_retraining_request_handler": True,
            "shadow_rollout_plans": True,
            "canary_rollout_plans": True,
            "health_gated_advancement": True,
            "explicit_rollback_targets": True,
            "automatic_training": False,
            "automatic_traffic_mutation": False,
        },
        "limits": {
            "trigger_signals": MAX_TRIGGER_SIGNALS,
            "health_gates": MAX_HEALTH_GATES,
            "rollout_steps": MAX_ROLLOUT_STEPS,
            "metadata_bytes": MAX_METADATA_BYTES,
        },
        "next_increment": "v4.3.3 Lifecycle Operations",
    }
