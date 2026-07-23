"""Dependency-light model lifecycle contracts for NFL Analytics Hub v4.3.0."""

from __future__ import annotations

import hashlib
import json
import math
import re
import threading
import time
from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import Any

VERSION = "4.3.0"
MAX_FEATURES = 128
MAX_TAGS = 20
MAX_POLICY_METRICS = 20
MAX_REQUIRED_CHECKS = 20
MAX_METADATA_BYTES = 256 * 1024
MAX_HISTORY_EVENTS = 50

_IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9._-]{0,79}$")
_VERSION = re.compile(r"^[a-z0-9][a-z0-9._+-]{0,63}$")
_SHA256 = re.compile(r"^(?:sha256:)?([a-f0-9]{64})$")
_FEATURE_TYPES = {"boolean", "category", "integer", "number", "string"}
_STATUSES = {"registered", "candidate", "champion", "retired", "archived"}
_TRANSITIONS = {
    "registered": {"candidate", "retired"},
    "candidate": {"registered", "champion", "retired"},
    "champion": {"retired"},
    "retired": {"archived"},
    "archived": set(),
}


def _canonical_json(value: Any, field: str, maximum_bytes: int) -> str:
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


def _digest(value: Any, field: str = "metadata") -> str:
    raw = _canonical_json(value, field, MAX_METADATA_BYTES)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _timestamp(value: Any, field: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a finite non-negative number") from exc
    if not math.isfinite(result) or result < 0:
        raise ValueError(f"{field} must be a finite non-negative number")
    return round(result, 6)


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


def _finite_number(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a finite number")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a finite number") from exc
    if not math.isfinite(result):
        raise ValueError(f"{field} must be a finite number")
    return round(result, 10)


def _identifier(value: Any, field: str) -> str:
    result = str(value or "").strip().lower()
    if not _IDENTIFIER.fullmatch(result):
        raise ValueError(f"{field} must use 1-80 lowercase letters, numbers, dots, dashes, or underscores")
    return result


def _version(value: Any) -> str:
    result = str(value or "").strip().lower()
    if not _VERSION.fullmatch(result):
        raise ValueError(
            "version must use 1-64 lowercase letters, numbers, dots, dashes, pluses, or underscores"
        )
    return result


def _text(value: Any, field: str, maximum: int, *, required: bool = True) -> str | None:
    result = str(value or "").strip()
    if not result:
        if required:
            raise ValueError(f"{field} is required")
        return None
    if len(result) > maximum:
        raise ValueError(f"{field} cannot exceed {maximum} characters")
    return result


def _boolean(value: Any, field: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{field} must be a boolean")
    return value


def _sha256(value: Any, field: str, *, required: bool = True) -> str | None:
    text = _text(value, field, 71, required=required)
    if text is None:
        return None
    match = _SHA256.fullmatch(text.lower())
    if not match:
        raise ValueError(f"{field} must be a SHA-256 digest")
    return f"sha256:{match.group(1)}"


def _identifier_list(
    value: Any,
    field: str,
    *,
    maximum: int,
    default: Sequence[str] = (),
) -> list[str]:
    items = list(default) if value is None else value
    if isinstance(items, str | bytes) or not isinstance(items, Sequence):
        raise ValueError(f"{field} must be a list")
    if len(items) > maximum:
        raise ValueError(f"{field} cannot contain more than {maximum} items")
    normalized = [_identifier(item, field.removesuffix("s") or field) for item in items]
    if len(set(normalized)) != len(normalized):
        raise ValueError(f"{field} cannot contain duplicates")
    return sorted(normalized)


def normalize_feature_schema(value: Any) -> list[dict[str, Any]]:
    """Normalize an inspectable, order-independent model feature schema."""
    if isinstance(value, str | bytes) or not isinstance(value, Sequence):
        raise ValueError("feature_schema must be a list")
    if len(value) > MAX_FEATURES:
        raise ValueError(f"feature_schema cannot contain more than {MAX_FEATURES} items")
    features: list[dict[str, Any]] = []
    names: set[str] = set()
    for item in value:
        if not isinstance(item, Mapping):
            raise ValueError("each feature_schema item must be a JSON object")
        name = _identifier(item.get("name"), "feature name")
        if name in names:
            raise ValueError(f"feature_schema contains duplicate feature {name}")
        names.add(name)
        data_type = str(item.get("data_type", "")).strip().lower()
        if data_type not in _FEATURE_TYPES:
            raise ValueError(f"feature {name} has an unsupported data_type")
        required = _boolean(item.get("required", True), f"feature {name} required")
        feature: dict[str, Any] = {
            "name": name,
            "data_type": data_type,
            "required": required,
            "source": _identifier(item.get("source", "input"), f"feature {name} source"),
        }
        description = _text(
            item.get("description"),
            f"feature {name} description",
            256,
            required=False,
        )
        if description is not None:
            feature["description"] = description
        if "default" in item:
            _canonical_json(item["default"], f"feature {name} default", 16 * 1024)
            feature["default"] = deepcopy(item["default"])
        features.append(feature)
    return sorted(features, key=lambda item: item["name"])


def _normalize_artifact(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ValueError("artifact must be a JSON object")
    return {
        "uri": _text(value.get("uri"), "artifact uri", 500),
        "digest": _sha256(value.get("digest"), "artifact digest"),
        "media_type": _text(
            value.get("media_type", "application/octet-stream"),
            "artifact media_type",
            120,
        ),
        "size_bytes": _integer(value.get("size_bytes", 0), "artifact size_bytes", 0, 10**12),
    }


def _normalize_training(value: Any) -> dict[str, Any]:
    if value is None:
        value = {}
    if not isinstance(value, Mapping):
        raise ValueError("training must be a JSON object")
    parameters = value.get("parameters", {})
    if not isinstance(parameters, Mapping):
        raise ValueError("training parameters must be a JSON object")
    _canonical_json(parameters, "training parameters", 64 * 1024)
    started_at = value.get("started_at")
    finished_at = value.get("finished_at")
    normalized_started = None if started_at is None else _timestamp(started_at, "training started_at")
    normalized_finished = None if finished_at is None else _timestamp(finished_at, "training finished_at")
    if (
        normalized_started is not None
        and normalized_finished is not None
        and normalized_finished < normalized_started
    ):
        raise ValueError("training finished_at cannot precede started_at")
    return {
        "dataset_digest": _sha256(
            value.get("dataset_digest"),
            "training dataset_digest",
            required=False,
        ),
        "code_version": _text(
            value.get("code_version"),
            "training code_version",
            120,
            required=False,
        ),
        "parameters": deepcopy(dict(parameters)),
        "started_at": normalized_started,
        "finished_at": normalized_finished,
    }


def normalize_model_version(
    payload: Mapping[str, Any],
    *,
    registered_at: float | None = None,
) -> dict[str, Any]:
    """Normalize one model version into the stable v4.3 registry contract."""
    if not isinstance(payload, Mapping):
        raise ValueError("model version must be a JSON object")
    model_key = _identifier(payload.get("model_key"), "model_key")
    version = _version(payload.get("version"))
    target = _identifier(payload.get("target"), "target")
    algorithm = _text(payload.get("algorithm"), "algorithm", 120)
    features = normalize_feature_schema(payload.get("feature_schema", []))
    artifact = _normalize_artifact(payload.get("artifact"))
    training = _normalize_training(payload.get("training"))
    tags = _identifier_list(payload.get("tags"), "tags", maximum=MAX_TAGS)
    description = _text(
        payload.get("description"),
        "description",
        1_000,
        required=False,
    )
    registered_by = _text(
        payload.get("registered_by", "system"),
        "registered_by",
        120,
    )
    timestamp = _timestamp(
        time.time() if registered_at is None else registered_at,
        "registered_at",
    )
    metadata = {
        "model_key": model_key,
        "version": version,
        "target": target,
        "algorithm": algorithm,
        "feature_schema": features,
        "artifact": artifact,
        "training": training,
        "tags": tags,
        "description": description,
    }
    metadata_digest = _digest(metadata)
    model_id = hashlib.sha256(model_key.encode("utf-8")).hexdigest()[:16]
    model_version_id = hashlib.sha256(f"{model_key}:{version}".encode()).hexdigest()[:20]
    schema_digest = _digest(features, "feature_schema")
    return {
        "contract_version": VERSION,
        "model_id": f"mdl_{model_id}",
        "model_version_id": f"mv_{model_version_id}",
        **metadata,
        "feature_schema_digest": f"sha256:{schema_digest}",
        "metadata_digest": f"sha256:{metadata_digest}",
        "status": "registered",
        "registered_at": timestamp,
        "registered_by": registered_by,
        "updated_at": timestamp,
        "promotion": None,
        "history": [],
    }


def _normalize_promotion_decision(
    value: Any,
    *,
    occurred_at: float,
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("promotion decision is required when a model becomes champion")
    passed = _boolean(value.get("passed"), "promotion decision passed")
    if not passed:
        raise ValueError("promotion decision must pass before a model becomes champion")
    evaluated_at = _timestamp(value.get("evaluated_at"), "promotion decision evaluated_at")
    if evaluated_at > occurred_at:
        raise ValueError("promotion decision evaluated_at cannot follow occurred_at")
    return {
        "policy_id": _identifier(value.get("policy_id"), "promotion decision policy_id"),
        "evaluation_id": _identifier(
            value.get("evaluation_id"),
            "promotion decision evaluation_id",
        ),
        "evidence_digest": _sha256(
            value.get("evidence_digest"),
            "promotion decision evidence_digest",
        ),
        "passed": True,
        "evaluated_at": evaluated_at,
    }


def transition_model_version(
    model_version: Mapping[str, Any],
    target_status: Any,
    *,
    occurred_at: float | None = None,
    actor: Any,
    reason: Any,
    promotion_decision: Any = None,
) -> dict[str, Any]:
    """Validate and apply one auditable model lifecycle transition."""
    if not isinstance(model_version, Mapping):
        raise ValueError("model_version must be a JSON object")
    if model_version.get("contract_version") != VERSION:
        raise ValueError(f"model_version must use contract_version {VERSION}")
    model_version_id = str(model_version.get("model_version_id", "")).strip().lower()
    if not re.fullmatch(r"mv_[a-f0-9]{20}", model_version_id):
        raise ValueError("model_version_id must be a normalized v4.3 identity")
    current = str(model_version.get("status", "")).strip().lower()
    target = str(target_status or "").strip().lower()
    if current not in _STATUSES:
        raise ValueError("model_version has an unsupported status")
    if target not in _STATUSES:
        raise ValueError("target_status is unsupported")
    if target not in _TRANSITIONS[current]:
        raise ValueError(f"cannot transition model version from {current} to {target}")
    normalized_actor = _text(actor, "actor", 120)
    normalized_reason = _text(reason, "reason", 500)
    timestamp = _timestamp(
        time.time() if occurred_at is None else occurred_at,
        "occurred_at",
    )
    previous_update = _timestamp(model_version.get("updated_at", 0), "updated_at")
    if timestamp < previous_update:
        raise ValueError("occurred_at cannot precede the model version updated_at")
    decision = (
        _normalize_promotion_decision(promotion_decision, occurred_at=timestamp)
        if target == "champion"
        else None
    )
    history = model_version.get("history", [])
    if not isinstance(history, list):
        raise ValueError("model_version history must be a list")
    sequence = len(history) + 1
    event_payload = {
        "model_version_id": model_version_id,
        "from_status": current,
        "to_status": target,
        "actor": normalized_actor,
        "reason": normalized_reason,
        "occurred_at": timestamp,
        "sequence": sequence,
        "promotion_decision": decision,
    }
    event_id = hashlib.sha256(
        _canonical_json(event_payload, "lifecycle event", 64 * 1024).encode("utf-8")
    ).hexdigest()[:24]
    event = {
        "contract_version": VERSION,
        "event_id": f"model_evt_{event_id}",
        "event_type": "model.lifecycle.transitioned",
        **event_payload,
    }
    updated = deepcopy(dict(model_version))
    updated["status"] = target
    updated["updated_at"] = timestamp
    updated["promotion"] = decision if target == "champion" else updated.get("promotion")
    updated["history"] = (deepcopy(history) + [event])[-MAX_HISTORY_EVENTS:]
    return updated


def normalize_promotion_policy(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize metric and integrity gates without fabricating an evaluation."""
    if not isinstance(payload, Mapping):
        raise ValueError("promotion policy must be a JSON object")
    model_key = _identifier(payload.get("model_key"), "model_key")
    target = _identifier(payload.get("target"), "target")
    metric_items = payload.get("metrics")
    if isinstance(metric_items, str | bytes) or not isinstance(metric_items, Sequence):
        raise ValueError("metrics must be a list")
    if not metric_items or len(metric_items) > MAX_POLICY_METRICS:
        raise ValueError(f"metrics must contain between 1 and {MAX_POLICY_METRICS} items")
    metrics: list[dict[str, Any]] = []
    metric_names: set[str] = set()
    for item in metric_items:
        if not isinstance(item, Mapping):
            raise ValueError("each metric must be a JSON object")
        name = _identifier(item.get("name"), "metric name")
        if name in metric_names:
            raise ValueError(f"metrics contains duplicate metric {name}")
        metric_names.add(name)
        direction = str(item.get("direction", "")).strip().lower()
        if direction not in {"higher", "lower"}:
            raise ValueError(f"metric {name} direction must be higher or lower")
        metrics.append(
            {
                "name": name,
                "direction": direction,
                "threshold": _finite_number(item.get("threshold"), f"metric {name} threshold"),
                "minimum_improvement": _finite_number(
                    item.get("minimum_improvement", 0),
                    f"metric {name} minimum_improvement",
                ),
            }
        )
    metrics.sort(key=lambda item: item["name"])
    required_checks = _identifier_list(
        payload.get("required_checks"),
        "required_checks",
        maximum=MAX_REQUIRED_CHECKS,
        default=("artifact.integrity", "feature.schema.compatibility"),
    )
    policy_body = {
        "model_key": model_key,
        "target": target,
        "metrics": metrics,
        "minimum_samples": _integer(
            payload.get("minimum_samples", 100),
            "minimum_samples",
            1,
            10_000_000,
        ),
        "maximum_evaluation_age_seconds": _integer(
            payload.get("maximum_evaluation_age_seconds", 7 * 24 * 60 * 60),
            "maximum_evaluation_age_seconds",
            60,
            90 * 24 * 60 * 60,
        ),
        "required_checks": required_checks,
        "require_all_metrics": _boolean(
            payload.get("require_all_metrics", True),
            "require_all_metrics",
        ),
        "allow_missing_champion": _boolean(
            payload.get("allow_missing_champion", False),
            "allow_missing_champion",
        ),
    }
    policy_id = hashlib.sha256(
        _canonical_json(policy_body, "promotion policy", 64 * 1024).encode("utf-8")
    ).hexdigest()[:24]
    return {
        "contract_version": VERSION,
        "policy_id": f"policy_{policy_id}",
        **policy_body,
    }


class InMemoryModelRegistry:
    """Bounded reference registry for tests and single-process development."""

    def __init__(self, max_versions: int = 1_000) -> None:
        self.max_versions = _integer(max_versions, "max_versions", 1, 10_000)
        self._versions: dict[str, dict[str, Any]] = {}
        self._order: list[str] = []
        self._lock = threading.RLock()

    def register(
        self,
        payload: Mapping[str, Any],
        *,
        registered_at: float | None = None,
    ) -> dict[str, Any]:
        candidate = normalize_model_version(payload, registered_at=registered_at)
        identity = candidate["model_version_id"]
        with self._lock:
            existing = self._versions.get(identity)
            if existing is not None:
                if existing["metadata_digest"] != candidate["metadata_digest"]:
                    raise ValueError("model key and version conflict with existing metadata")
                return {
                    "accepted": False,
                    "deduplicated": True,
                    "model_version": deepcopy(existing),
                }
            while len(self._order) >= self.max_versions:
                removed = self._order.pop(0)
                self._versions.pop(removed, None)
            self._versions[identity] = candidate
            self._order.append(identity)
            return {
                "accepted": True,
                "deduplicated": False,
                "model_version": deepcopy(candidate),
            }

    def get(self, model_version_id: Any) -> dict[str, Any] | None:
        with self._lock:
            record = self._versions.get(str(model_version_id))
            return deepcopy(record) if record is not None else None

    def transition(
        self,
        model_version_id: Any,
        target_status: Any,
        **kwargs: Any,
    ) -> dict[str, Any]:
        identity = str(model_version_id)
        with self._lock:
            current = self._versions.get(identity)
            if current is None:
                raise KeyError("model version not found")
            updated = transition_model_version(current, target_status, **kwargs)
            self._versions[identity] = updated
            return deepcopy(updated)


def lifecycle_manifest() -> dict[str, Any]:
    return {
        "version": VERSION,
        "name": "Model Lifecycle and Governance",
        "features": {
            "deterministic_model_identities": True,
            "versioned_feature_schemas": True,
            "artifact_integrity_metadata": True,
            "training_provenance": True,
            "conflict_safe_registration": True,
            "validated_lifecycle_transitions": True,
            "evidence_bearing_promotions": True,
            "promotion_policy_contracts": True,
            "automated_evaluation": False,
            "champion_challenger_automation": False,
        },
        "statuses": sorted(_STATUSES),
        "limits": {
            "features": MAX_FEATURES,
            "tags": MAX_TAGS,
            "policy_metrics": MAX_POLICY_METRICS,
            "required_checks": MAX_REQUIRED_CHECKS,
            "metadata_bytes": MAX_METADATA_BYTES,
            "history_events": MAX_HISTORY_EVENTS,
        },
        "next_increment": "v4.3.1 Automated Evaluation and Champion/Challenger Selection",
    }
