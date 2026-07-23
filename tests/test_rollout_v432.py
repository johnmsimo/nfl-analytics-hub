import hashlib
from copy import deepcopy

import pytest

from evaluation_v431 import run_held_out_evaluation
from execution_v422 import ExecutionContext, TypedHandlerRegistry
from lifecycle_v43 import (
    normalize_model_version,
    normalize_promotion_policy,
    transition_model_version,
)
from rollout_v432 import (
    _digest,
    build_retraining_request,
    evaluate_retraining_triggers,
    evaluate_rollout_step,
    normalize_retraining_policy,
    normalize_rollout_plan,
    rollout_manifest,
)

_ARTIFACT = "a" * 64
_CHAMPION_ARTIFACT = "b" * 64
_DATASET = "c" * 64
_EVIDENCE = "e" * 64


def _registered(version: str, artifact: str = _ARTIFACT):
    return normalize_model_version(
        {
            "model_key": "win-probability",
            "version": version,
            "target": "home-win",
            "algorithm": "calibrated-ensemble",
            "feature_schema": [
                {
                    "name": "home-epa",
                    "data_type": "number",
                    "source": "warehouse",
                }
            ],
            "artifact": {
                "uri": f"s3://models/win-probability/{version}.bin",
                "digest": artifact,
            },
        },
        registered_at=80.0,
    )


def _candidate(version: str = "v8"):
    return transition_model_version(
        _registered(version),
        "candidate",
        occurred_at=81.0,
        actor="evaluation-worker",
        reason="ready for evaluation",
    )


def _champion(version: str = "v7"):
    candidate = transition_model_version(
        _registered(version, _CHAMPION_ARTIFACT),
        "candidate",
        occurred_at=81.0,
        actor="evaluation-worker",
        reason="prior evaluation",
    )
    return transition_model_version(
        candidate,
        "champion",
        occurred_at=82.0,
        actor="release-manager",
        reason="prior champion",
        promotion_decision={
            "policy_id": "policy-prior",
            "evaluation_id": "eval-prior",
            "evidence_digest": _EVIDENCE,
            "passed": True,
            "evaluated_at": 81.5,
        },
    )


def _evaluation(candidate=None, champion=None, *, threshold=0.2):
    candidate = candidate or _candidate()
    champion = champion or _champion()
    return run_held_out_evaluation(
        {
            "policy": normalize_promotion_policy(
                {
                    "model_key": "win-probability",
                    "target": "home-win",
                    "minimum_samples": 3,
                    "maximum_evaluation_age_seconds": 120,
                    "metrics": [
                        {
                            "name": "brier-score",
                            "direction": "lower",
                            "threshold": threshold,
                            "minimum_improvement": 0.01,
                        }
                    ],
                }
            ),
            "challenger": candidate,
            "champion": champion,
            "dataset_digest": _DATASET,
            "window": {"started_at": 80.0, "finished_at": 99.0},
            "observed_artifact_digest": _ARTIFACT,
            "expected_feature_schema_digest": candidate["feature_schema_digest"],
            "observations": [
                {"actual": 1, "candidate_prediction": 0.9, "champion_prediction": 0.7},
                {"actual": 0, "candidate_prediction": 0.1, "champion_prediction": 0.4},
                {"actual": 1, "candidate_prediction": 0.8, "champion_prediction": 0.6},
            ],
        },
        evaluated_at=100.0,
    )


def _retraining_policy(**overrides):
    payload = {
        "model_key": "win-probability",
        "maximum_signal_age_seconds": 60,
        "cooldown_seconds": 120,
        "signals": [
            {
                "name": "brier-degradation",
                "kind": "performance-degradation",
                "direction": "higher",
                "threshold": 0.03,
                "minimum_samples": 100,
            },
            {
                "name": "feature-psi",
                "kind": "feature-drift",
                "direction": "higher",
                "threshold": 0.2,
                "minimum_samples": 100,
            },
        ],
    }
    payload.update(overrides)
    return normalize_retraining_policy(payload)


def _trigger(**overrides):
    payload = {
        "policy": _retraining_policy(),
        "model_version": _champion(),
        "signals": [
            {
                "name": "brier-degradation",
                "value": 0.04,
                "sample_count": 500,
                "observed_at": 99.0,
                "evidence_digest": _EVIDENCE,
            },
            {
                "name": "feature-psi",
                "value": 0.1,
                "sample_count": 500,
                "observed_at": 99.0,
                "evidence_digest": _EVIDENCE,
            },
        ],
    }
    payload.update(overrides)
    return evaluate_retraining_triggers(payload, evaluated_at=100.0)


def _request(trigger=None, model=None):
    return build_retraining_request(
        {
            "trigger": trigger or _trigger(),
            "model_version": model or _champion(),
            "requested_version": "v9",
            "dataset_digest": _DATASET,
            "code_version": "git:abc123",
            "parameters": {"trees": 250},
            "output_artifact_uri": "s3://models/win-probability/v9.bin",
            "requested_by": "model-operator",
        },
        requested_at=101.0,
    )


def _plan(*, mode="canary", health_gates=None, steps=None, threshold=0.2):
    candidate = _candidate()
    champion = _champion()
    if steps is None:
        steps = [
            {
                "name": "five-percent",
                "candidate_traffic_percent": 5,
                "minimum_observation_seconds": 60,
            },
            {
                "name": "full",
                "candidate_traffic_percent": 100,
                "minimum_observation_seconds": 60,
            },
        ]
    if health_gates is None:
        health_gates = [
            {
                "name": "error-rate",
                "direction": "lower",
                "threshold": 0.02,
                "minimum_samples": 100,
                "maximum_age_seconds": 60,
                "breach_action": "rollback",
            }
        ]
    return normalize_rollout_plan(
        {
            "candidate": candidate,
            "champion": champion,
            "evaluation": _evaluation(candidate, champion, threshold=threshold),
            "selection_decided_at": 101.0,
            "mode": mode,
            "steps": steps,
            "health_gates": health_gates,
        },
        planned_at=102.0,
    )


def _health(value=0.01):
    return [
        {
            "name": "error-rate",
            "value": value,
            "sample_count": 500,
            "observed_at": 199.0,
            "evidence_digest": _EVIDENCE,
        }
    ]


def test_retraining_policy_is_deterministic_and_bounded():
    first = _retraining_policy()
    second = _retraining_policy(signals=list(reversed(first["signals"])))
    assert first == second
    assert first["policy_id"].startswith("retrain_policy_")
    with pytest.raises(ValueError, match="duplicate signal"):
        _retraining_policy(signals=[first["signals"][0], first["signals"][0]])
    with pytest.raises(ValueError, match="unsupported kind"):
        _retraining_policy(signals=[{"name": "mystery", "kind": "unknown", "threshold": 1}])


def test_any_signal_can_trigger_with_fresh_sampled_evidence():
    first = _trigger()
    second = _trigger()
    assert first == second
    assert first["triggered"] is True
    assert first["trigger_id"].startswith("trigger_")
    assert [item["triggered"] for item in first["signals"]] == [True, False]


def test_all_signal_policy_requires_every_threshold_breach():
    result = _trigger(policy=_retraining_policy(require_all_signals=True))
    assert result["triggered"] is False
    assert result["gates"]["signal_policy"] is False


def test_stale_samples_and_cooldown_block_retraining():
    stale = deepcopy(_trigger()["signals"])
    stale[0]["observed_at"] = 1.0
    result = _trigger(signals=stale)
    assert result["triggered"] is False
    assert result["signals"][0]["fresh"] is False
    cooling = _trigger(last_requested_at=50.0)
    assert cooling["triggered"] is False
    assert cooling["cooldown"]["remaining_seconds"] == 70.0


def test_observed_signals_must_match_policy_exactly():
    with pytest.raises(ValueError, match="match"):
        _trigger(signals=[_trigger()["signals"][0]])


def test_retraining_request_uses_stable_distributed_job_contract():
    request = _request()
    assert request["request_id"].startswith("retrain_")
    assert request["job"]["version"] == "4.2.0"
    assert request["job"]["job_type"] == "model.retraining.request"
    assert request["job"]["namespace"] == "model-lifecycle"
    assert request["job"]["payload"]["request_contract_version"] == "4.3.2"


def test_retraining_request_rejects_tampered_or_nonpassing_trigger():
    changed = deepcopy(_trigger())
    changed["triggered"] = False
    with pytest.raises(ValueError, match="integrity"):
        _request(trigger=changed)
    nonpassing = _trigger(policy=_retraining_policy(require_all_signals=True))
    with pytest.raises(ValueError, match="passing trigger"):
        _request(trigger=nonpassing)


def test_retraining_request_rejects_rehashed_internally_inconsistent_trigger():
    changed = deepcopy(_trigger())
    changed["signals"][0]["value"] = 0.0
    body = {key: value for key, value in changed.items() if key not in {"trigger_id", "evidence_digest"}}
    changed["evidence_digest"] = _digest(body, "retraining trigger evaluation")
    changed["trigger_id"] = "trigger_" + hashlib.sha256(changed["evidence_digest"].encode()).hexdigest()[:24]
    with pytest.raises(ValueError, match="inconsistent"):
        _request(trigger=changed)


def test_retraining_request_is_conflict_safe_and_time_ordered():
    first = _request()
    second = _request()
    assert first == second
    with pytest.raises(ValueError, match="cannot precede"):
        build_retraining_request(
            {
                "trigger": _trigger(),
                "model_version": _champion(),
                "requested_version": "v9",
                "dataset_digest": _DATASET,
                "code_version": "git:abc123",
                "output_artifact_uri": "s3://models/v9.bin",
                "requested_by": "operator",
            },
            requested_at=99.0,
        )


def test_typed_handler_validates_request_without_claiming_training():
    job = _request()["job"]
    registry = TypedHandlerRegistry()
    validated = registry.validate(job)
    assert validated["family"] == "model-lifecycle"
    result = registry.execute(
        job,
        ExecutionContext(job["job_id"], 10.0, lambda _job_id: False, lambda: 0.0),
    )
    assert result["request_validated"] is True
    assert result["automatic_training"] is False
    assert "artifact_digest" not in result


def test_canary_plan_is_evidence_bound_and_has_explicit_rollback_target():
    plan = _plan()
    assert plan["mode"] == "canary"
    assert plan["steps"][-1]["candidate_traffic_percent"] == 100
    assert plan["rollback_target"]["artifact_digest"] == f"sha256:{_CHAMPION_ARTIFACT}"
    assert plan["automatic_traffic_mutation"] is False
    assert plan["promotion_decision"]["passed"] is True


def test_shadow_plan_requires_zero_serving_traffic_and_increasing_mirroring():
    plan = _plan(
        mode="shadow",
        steps=[
            {
                "name": "half-shadow",
                "shadow_traffic_percent": 50,
                "minimum_observation_seconds": 60,
            },
            {
                "name": "full-shadow",
                "shadow_traffic_percent": 100,
                "minimum_observation_seconds": 60,
            },
        ],
    )
    assert plan["steps"][0]["candidate_traffic_percent"] == 0
    with pytest.raises(ValueError, match="zero candidate traffic"):
        _plan(
            mode="shadow",
            steps=[
                {
                    "name": "unsafe",
                    "candidate_traffic_percent": 5,
                    "shadow_traffic_percent": 50,
                }
            ],
        )


def test_canary_plan_requires_full_terminal_step_and_passing_selection():
    with pytest.raises(ValueError, match="100 percent"):
        _plan(
            steps=[
                {
                    "name": "partial",
                    "candidate_traffic_percent": 10,
                }
            ]
        )
    with pytest.raises(ValueError, match="passing"):
        _plan(threshold=0.001)


def test_rollout_step_advances_and_completes_after_health_and_duration_pass():
    plan = _plan()
    first = evaluate_rollout_step(
        {
            "rollout_plan": plan,
            "step_index": 0,
            "step_started_at": 120.0,
            "health_observations": _health(),
        },
        evaluated_at=200.0,
    )
    assert first["action"] == "advance"
    assert first["next_step_index"] == 1
    final = evaluate_rollout_step(
        {
            "rollout_plan": plan,
            "step_index": 1,
            "step_started_at": 120.0,
            "health_observations": _health(),
        },
        evaluated_at=200.0,
    )
    assert final["action"] == "complete"
    assert final["automatic_traffic_mutation"] is False


def test_rollout_step_holds_until_observation_duration_elapses():
    result = evaluate_rollout_step(
        {
            "rollout_plan": _plan(),
            "step_index": 0,
            "step_started_at": 150.0,
            "health_observations": _health(),
        },
        evaluated_at=200.0,
    )
    assert result["action"] == "hold"
    assert result["duration_passed"] is False


def test_failed_health_gate_emits_explicit_rollback_target():
    plan = _plan()
    result = evaluate_rollout_step(
        {
            "rollout_plan": plan,
            "step_index": 0,
            "step_started_at": 120.0,
            "health_observations": _health(0.2),
        },
        evaluated_at=200.0,
    )
    assert result["action"] == "rollback"
    assert result["rollback_target"] == plan["rollback_target"]
    assert result["automatic_traffic_mutation"] is False


def test_hold_gate_does_not_emit_rollback_target():
    plan = _plan(
        health_gates=[
            {
                "name": "error-rate",
                "direction": "lower",
                "threshold": 0.02,
                "breach_action": "hold",
            }
        ]
    )
    result = evaluate_rollout_step(
        {
            "rollout_plan": plan,
            "step_index": 0,
            "step_started_at": 120.0,
            "health_observations": _health(0.2),
        },
        evaluated_at=200.0,
    )
    assert result["action"] == "hold"
    assert result["rollback_target"] is None


def test_stale_health_evidence_holds_instead_of_rolling_back():
    stale = _health(0.2)
    stale[0]["observed_at"] = 1.0
    result = evaluate_rollout_step(
        {
            "rollout_plan": _plan(),
            "step_index": 0,
            "step_started_at": 120.0,
            "health_observations": stale,
        },
        evaluated_at=200.0,
    )
    assert result["action"] == "hold"
    assert result["rollback_target"] is None
    assert "health evidence" in result["reasons"][0]


def test_rollout_step_rejects_mutated_plan_and_missing_health_evidence():
    changed = deepcopy(_plan())
    changed["steps"][0]["candidate_traffic_percent"] = 99
    with pytest.raises(ValueError, match="integrity"):
        evaluate_rollout_step(
            {
                "rollout_plan": changed,
                "step_index": 0,
                "step_started_at": 120.0,
                "health_observations": _health(),
            },
            evaluated_at=200.0,
        )
    with pytest.raises(ValueError, match="match"):
        evaluate_rollout_step(
            {
                "rollout_plan": _plan(),
                "step_index": 0,
                "step_started_at": 120.0,
                "health_observations": [
                    {
                        "name": "latency",
                        "value": 1,
                        "sample_count": 500,
                        "observed_at": 199.0,
                        "evidence_digest": _EVIDENCE,
                    }
                ],
            },
            evaluated_at=200.0,
        )


def test_manifest_discloses_automation_boundaries():
    manifest = rollout_manifest()
    assert manifest["version"] == "4.3.2"
    assert manifest["job_contract_version"] == "4.2.0"
    assert manifest["features"]["distributed_retraining_requests"] is True
    assert manifest["features"]["automatic_training"] is False
    assert manifest["features"]["automatic_traffic_mutation"] is False
