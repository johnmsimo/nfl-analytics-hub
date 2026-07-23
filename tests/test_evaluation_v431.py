import hashlib
import json
from copy import deepcopy

import pytest

from evaluation_v431 import (
    evaluation_manifest,
    evaluation_metric_catalog,
    run_held_out_evaluation,
    select_champion_challenger,
)
from lifecycle_v43 import (
    normalize_model_version,
    normalize_promotion_policy,
    transition_model_version,
)

_ARTIFACT = "a" * 64
_DATASET = "c" * 64
_EVIDENCE = "e" * 64


def _registered(version: str):
    return normalize_model_version(
        {
            "model_key": "player-props",
            "version": version,
            "target": "passing-yards",
            "algorithm": "calibrated-ensemble",
            "feature_schema": [
                {
                    "name": "opponent-epa",
                    "data_type": "number",
                    "source": "warehouse",
                }
            ],
            "artifact": {
                "uri": f"s3://models/player-props/{version}.bin",
                "digest": _ARTIFACT,
                "size_bytes": 1_024,
            },
        },
        registered_at=90.0,
    )


def _candidate(version: str = "v8"):
    return transition_model_version(
        _registered(version),
        "candidate",
        occurred_at=91.0,
        actor="evaluation-worker",
        reason="ready for held-out evaluation",
    )


def _champion(version: str = "v7"):
    candidate = _candidate(version)
    return transition_model_version(
        candidate,
        "champion",
        occurred_at=92.0,
        actor="release-manager",
        reason="prior approved champion",
        promotion_decision={
            "policy_id": "policy-prior",
            "evaluation_id": "eval-prior",
            "evidence_digest": _EVIDENCE,
            "passed": True,
            "evaluated_at": 91.5,
        },
    )


def _policy(**overrides):
    payload = {
        "model_key": "player-props",
        "target": "passing-yards",
        "minimum_samples": 3,
        "maximum_evaluation_age_seconds": 120,
        "metrics": [
            {
                "name": "mae",
                "direction": "lower",
                "threshold": 2.0,
                "minimum_improvement": 0.5,
            },
            {
                "name": "rmse",
                "direction": "lower",
                "threshold": 2.0,
                "minimum_improvement": 0.5,
            },
        ],
    }
    payload.update(overrides)
    return normalize_promotion_policy(payload)


def _payload(**overrides):
    challenger = _candidate()
    payload = {
        "policy": _policy(),
        "challenger": challenger,
        "champion": _champion(),
        "dataset_digest": _DATASET,
        "window": {"started_at": 80.0, "finished_at": 99.0},
        "observed_artifact_digest": _ARTIFACT,
        "expected_feature_schema_digest": challenger["feature_schema_digest"],
        "observations": [
            {"actual": 10, "candidate_prediction": 11, "champion_prediction": 13},
            {"actual": 20, "candidate_prediction": 19, "champion_prediction": 17},
            {"actual": 30, "candidate_prediction": 29, "champion_prediction": 34},
        ],
    }
    payload.update(overrides)
    return payload


def _evaluation(**overrides):
    return run_held_out_evaluation(_payload(**overrides), evaluated_at=100.0)


def test_metric_catalog_is_bounded_and_explicit():
    catalog = evaluation_metric_catalog()
    assert catalog["contract_version"] == "4.3.1"
    assert [item["name"] for item in catalog["metrics"]] == [
        "accuracy",
        "brier-score",
        "calibration-error",
        "log-loss",
        "mae",
        "rmse",
    ]
    assert catalog["maximum_observations"] == 50_000


def test_held_out_evaluation_is_deterministic_and_passes_policy():
    first = _evaluation()
    second = _evaluation()
    assert first == second
    assert first["evaluation_id"].startswith("eval_")
    assert first["evidence_digest"].startswith("sha256:")
    assert first["passed"] is True
    assert first["sample_count"] == 3
    assert all(first["gates"].values())
    assert [item["candidate_value"] for item in first["metrics"]] == [1.0, 1.0]
    assert all(item["improvement"] > 0.5 for item in first["metrics"])


def test_evaluation_rejects_unsupported_metric():
    policy = _policy(
        metrics=[
            {
                "name": "mape",
                "direction": "lower",
                "threshold": 0.1,
            }
        ]
    )
    with pytest.raises(ValueError, match="unsupported evaluation metrics"):
        _evaluation(policy=policy)


def test_classification_metrics_require_probabilities_and_binary_actuals():
    policy = _policy(
        metrics=[
            {"name": "brier-score", "direction": "lower", "threshold": 0.3},
            {"name": "log-loss", "direction": "lower", "threshold": 0.7},
            {"name": "accuracy", "direction": "higher", "threshold": 0.5},
            {
                "name": "calibration-error",
                "direction": "lower",
                "threshold": 0.3,
            },
        ]
    )
    observations = [
        {"actual": 1, "candidate_prediction": 0.9, "champion_prediction": 0.7},
        {"actual": 0, "candidate_prediction": 0.1, "champion_prediction": 0.4},
        {"actual": 1, "candidate_prediction": 0.8, "champion_prediction": 0.6},
    ]
    result = _evaluation(policy=policy, observations=observations)
    assert result["passed"] is True
    assert {item["name"] for item in result["metrics"]} == {
        "accuracy",
        "brier-score",
        "calibration-error",
        "log-loss",
    }
    invalid = deepcopy(observations)
    invalid[0]["candidate_prediction"] = 1.2
    with pytest.raises(ValueError, match="between 0 and 1"):
        _evaluation(policy=policy, observations=invalid)


def test_minimum_sample_gate_fails_without_fabricating_samples():
    policy = _policy(minimum_samples=4)
    result = _evaluation(policy=policy)
    assert result["passed"] is False
    assert result["sample_count"] == 3
    assert result["gates"]["minimum_samples"] is False


def test_artifact_and_schema_checks_require_matching_observed_evidence():
    missing_artifact = _evaluation(observed_artifact_digest=None)
    assert missing_artifact["passed"] is False
    assert missing_artifact["checks"][0]["passed"] is False
    wrong_schema = _evaluation(expected_feature_schema_digest="d" * 64)
    assert wrong_schema["passed"] is False
    assert wrong_schema["checks"][1]["passed"] is False


def test_custom_required_check_requires_passing_evidence():
    policy = _policy(required_checks=["lineage.approved"])
    failed = _evaluation(policy=policy)
    assert failed["checks"][0]["passed"] is False
    passed = _evaluation(
        policy=policy,
        check_results={
            "lineage.approved": {
                "passed": True,
                "evidence_digest": _EVIDENCE,
            }
        },
    )
    assert passed["passed"] is True


def test_policy_can_explicitly_allow_evaluation_without_champion():
    policy = _policy(allow_missing_champion=True)
    observations = [
        {"actual": 10, "candidate_prediction": 11},
        {"actual": 20, "candidate_prediction": 19},
        {"actual": 30, "candidate_prediction": 29},
    ]
    result = _evaluation(policy=policy, champion=None, observations=observations)
    assert result["passed"] is True
    assert result["champion_model_version_id"] is None
    assert all(item["champion_value"] is None for item in result["metrics"])


def test_missing_champion_is_rejected_when_policy_requires_one():
    with pytest.raises(ValueError, match="does not allow"):
        _evaluation(champion=None)


def test_policy_identity_tampering_is_rejected():
    policy = _policy()
    policy["policy_id"] = "policy_" + "f" * 24
    with pytest.raises(ValueError, match="policy_id"):
        _evaluation(policy=policy)


def test_fresh_passing_evaluation_selects_challenger():
    evaluation = _evaluation()
    selection = select_champion_challenger(
        {"evaluation": evaluation},
        decided_at=101.0,
    )
    assert selection["action"] == "promote_challenger"
    assert selection["selected_model_version_id"] == evaluation["challenger_model_version_id"]
    assert selection["promotion_decision"] == {
        "policy_id": evaluation["policy_id"],
        "evaluation_id": evaluation["evaluation_id"],
        "evidence_digest": evaluation["evidence_digest"],
        "passed": True,
        "evaluated_at": 100.0,
    }


def test_selection_decision_integrates_with_champion_transition():
    challenger = _candidate()
    evaluation = _evaluation(challenger=challenger)
    selection = select_champion_challenger(
        {"evaluation": evaluation},
        decided_at=101.0,
    )
    champion = transition_model_version(
        challenger,
        "champion",
        occurred_at=102.0,
        actor="release-manager",
        reason="v4.3.1 evaluation passed",
        promotion_decision=selection["promotion_decision"],
    )
    assert champion["status"] == "champion"
    assert champion["promotion"]["evaluation_id"] == evaluation["evaluation_id"]


def test_failed_evaluation_retains_existing_champion():
    policy = _policy(metrics=[{"name": "mae", "direction": "lower", "threshold": 0.1}])
    evaluation = _evaluation(policy=policy)
    selection = select_champion_challenger(
        {"evaluation": evaluation},
        decided_at=101.0,
    )
    assert selection["action"] == "retain_champion"
    assert selection["selected_model_version_id"] == evaluation["champion_model_version_id"]
    assert selection["promotion_decision"]["passed"] is False


def test_stale_evaluation_retains_existing_champion():
    evaluation = _evaluation()
    selection = select_champion_challenger(
        {"evaluation": evaluation},
        decided_at=221.0,
    )
    assert selection["action"] == "retain_champion"
    assert selection["freshness"]["passed"] is False
    assert selection["promotion_decision"]["passed"] is False


def test_selection_rejects_mutated_evidence_and_backward_time():
    evaluation = _evaluation()
    changed = deepcopy(evaluation)
    changed["metrics"][0]["candidate_value"] = 999
    with pytest.raises(ValueError, match="integrity"):
        select_champion_challenger({"evaluation": changed}, decided_at=101.0)
    with pytest.raises(ValueError, match="cannot precede"):
        select_champion_challenger({"evaluation": evaluation}, decided_at=99.0)


def test_selection_rejects_rehashed_but_internally_inconsistent_evidence():
    evaluation = _evaluation()
    evaluation["gates"]["policy_metrics"] = False
    body = deepcopy(evaluation)
    body.pop("evaluation_id")
    body.pop("evidence_digest")
    raw = json.dumps(
        body,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    evidence_digest = f"sha256:{hashlib.sha256(raw.encode()).hexdigest()}"
    evaluation["evidence_digest"] = evidence_digest
    evaluation["evaluation_id"] = f"eval_{hashlib.sha256(evidence_digest.encode()).hexdigest()[:24]}"
    with pytest.raises(ValueError, match="gates are inconsistent"):
        select_champion_challenger({"evaluation": evaluation}, decided_at=101.0)


def test_manifest_advances_only_evaluation_contract():
    manifest = evaluation_manifest()
    assert manifest["version"] == "4.3.1"
    assert manifest["registry_contract_version"] == "4.3.0"
    assert manifest["features"]["evidence_backed_promotion_decisions"] is True
    assert manifest["features"]["automatic_lifecycle_mutation"] is False
    assert "v4.3.2" in manifest["next_increment"]
