import math

import pytest

from lifecycle_v43 import (
    InMemoryModelRegistry,
    lifecycle_manifest,
    normalize_model_version,
    normalize_promotion_policy,
    transition_model_version,
)

_DIGEST_A = "a" * 64
_DIGEST_B = "b" * 64


def _model(**overrides):
    payload = {
        "model_key": "player-props",
        "version": "v7",
        "target": "passing-yards",
        "algorithm": "calibrated-gradient-boosting",
        "feature_schema": [
            {
                "name": "opponent-epa",
                "data_type": "number",
                "source": "warehouse",
            },
            {
                "name": "attempts-rolling-4",
                "data_type": "number",
                "source": "warehouse",
            },
        ],
        "artifact": {
            "uri": "s3://models/player-props/v7.bin",
            "digest": _DIGEST_A,
            "size_bytes": 1024,
        },
        "training": {
            "dataset_digest": _DIGEST_B,
            "code_version": "git:abc123",
            "parameters": {"trees": 100},
            "started_at": 90.0,
            "finished_at": 99.0,
        },
        "registered_by": "model-worker",
    }
    payload.update(overrides)
    return normalize_model_version(payload, registered_at=100.0)


def _policy(**overrides):
    payload = {
        "model_key": "player-props",
        "target": "passing-yards",
        "minimum_samples": 500,
        "metrics": [
            {
                "name": "mae",
                "direction": "lower",
                "threshold": 18.5,
                "minimum_improvement": 0.5,
            },
            {
                "name": "calibration-error",
                "direction": "lower",
                "threshold": 0.04,
            },
        ],
    }
    payload.update(overrides)
    return normalize_promotion_policy(payload)


def test_model_version_normalization_is_deterministic_and_inspectable():
    first = _model()
    second = _model()
    assert first == second
    assert first["model_id"].startswith("mdl_")
    assert first["model_version_id"].startswith("mv_")
    assert first["metadata_digest"].startswith("sha256:")
    assert first["feature_schema_digest"].startswith("sha256:")
    assert first["status"] == "registered"


def test_feature_order_does_not_change_registry_fingerprint():
    first = _model()
    second = _model(feature_schema=list(reversed(first["feature_schema"])))
    assert first["metadata_digest"] == second["metadata_digest"]
    assert first["feature_schema"] == second["feature_schema"]


def test_artifact_and_training_digests_are_normalized():
    model = _model()
    assert model["artifact"]["digest"] == f"sha256:{_DIGEST_A}"
    assert model["training"]["dataset_digest"] == f"sha256:{_DIGEST_B}"


def test_duplicate_features_are_rejected():
    feature = {"name": "epa", "data_type": "number"}
    with pytest.raises(ValueError, match="duplicate feature"):
        _model(feature_schema=[feature, feature])


def test_non_finite_training_parameters_are_rejected():
    with pytest.raises(ValueError, match="finite"):
        _model(training={"parameters": {"learning_rate": math.nan}})


def test_training_end_cannot_precede_start():
    with pytest.raises(ValueError, match="cannot precede"):
        _model(training={"started_at": 20.0, "finished_at": 10.0})


def test_candidate_transition_is_auditable():
    candidate = transition_model_version(
        _model(),
        "candidate",
        occurred_at=101.0,
        actor="release-manager",
        reason="ready for held-out evaluation",
    )
    assert candidate["status"] == "candidate"
    assert candidate["history"][0]["from_status"] == "registered"
    assert candidate["history"][0]["to_status"] == "candidate"
    assert candidate["history"][0]["event_id"].startswith("model_evt_")


def test_champion_transition_requires_passing_evidence():
    candidate = transition_model_version(
        _model(),
        "candidate",
        occurred_at=101.0,
        actor="release-manager",
        reason="ready for evaluation",
    )
    with pytest.raises(ValueError, match="promotion decision"):
        transition_model_version(
            candidate,
            "champion",
            occurred_at=102.0,
            actor="release-manager",
            reason="promote",
        )
    with pytest.raises(ValueError, match="must pass"):
        transition_model_version(
            candidate,
            "champion",
            occurred_at=102.0,
            actor="release-manager",
            reason="promote",
            promotion_decision={
                "policy_id": "policy-weekly",
                "evaluation_id": "eval-13",
                "evidence_digest": _DIGEST_A,
                "passed": False,
                "evaluated_at": 101.5,
            },
        )


def test_champion_transition_preserves_promotion_evidence():
    candidate = transition_model_version(
        _model(),
        "candidate",
        occurred_at=101.0,
        actor="release-manager",
        reason="ready for evaluation",
    )
    champion = transition_model_version(
        candidate,
        "champion",
        occurred_at=102.0,
        actor="release-manager",
        reason="all policy gates passed",
        promotion_decision={
            "policy_id": "policy-weekly",
            "evaluation_id": "eval-13",
            "evidence_digest": _DIGEST_A,
            "passed": True,
            "evaluated_at": 101.5,
        },
    )
    assert champion["status"] == "champion"
    assert champion["promotion"]["evaluation_id"] == "eval-13"
    assert champion["history"][-1]["promotion_decision"]["passed"] is True


def test_invalid_lifecycle_jump_is_rejected():
    with pytest.raises(ValueError, match="cannot transition"):
        transition_model_version(
            _model(),
            "champion",
            occurred_at=101.0,
            actor="release-manager",
            reason="skip evaluation",
        )


def test_transition_rejects_unknown_contract_versions():
    model = _model()
    model["contract_version"] = "4.2.3"
    with pytest.raises(ValueError, match="contract_version 4.3.0"):
        transition_model_version(
            model,
            "candidate",
            occurred_at=101.0,
            actor="release-manager",
            reason="wrong contract",
        )


def test_promotion_policy_is_deterministic_and_explicit():
    first = _policy()
    second = _policy(metrics=list(reversed(first["metrics"])))
    assert first == second
    assert first["policy_id"].startswith("policy_")
    assert first["minimum_samples"] == 500
    assert first["required_checks"] == [
        "artifact.integrity",
        "feature.schema.compatibility",
    ]


def test_promotion_policy_rejects_invalid_metric_contracts():
    with pytest.raises(ValueError, match="direction"):
        _policy(metrics=[{"name": "mae", "direction": "best", "threshold": 10}])
    with pytest.raises(ValueError, match="duplicate metric"):
        _policy(
            metrics=[
                {"name": "mae", "direction": "lower", "threshold": 10},
                {"name": "mae", "direction": "lower", "threshold": 11},
            ]
        )


def test_registry_deduplicates_and_detects_metadata_conflicts():
    registry = InMemoryModelRegistry()
    payload = {
        "model_key": "win-probability",
        "version": "v1",
        "target": "home-win",
        "algorithm": "logistic-regression",
    }
    assert registry.register(payload, registered_at=100.0)["accepted"] is True
    assert registry.register(payload, registered_at=101.0)["deduplicated"] is True
    with pytest.raises(ValueError, match="conflict"):
        registry.register(
            {**payload, "algorithm": "gradient-boosting"},
            registered_at=102.0,
        )


def test_manifest_discloses_v431_automation_boundary():
    manifest = lifecycle_manifest()
    assert manifest["version"] == "4.3.0"
    assert manifest["features"]["promotion_policy_contracts"] is True
    assert manifest["features"]["automated_evaluation"] is False
    assert "v4.3.1" in manifest["next_increment"]
