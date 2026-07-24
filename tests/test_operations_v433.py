import pytest

from lifecycle_v43 import transition_model_version
from operations_v433 import (
    InMemoryLifecycleOperations,
    approval_evidence_for_transition,
    build_lifecycle_operations,
    decide_approval,
    normalize_approval_request,
    normalize_health_observation,
    operations_manifest,
)

_ARTIFACT = "a" * 64
_EVIDENCE = "e" * 64


def _model(version="v8"):
    return {
        "model_key": "win-probability",
        "version": version,
        "target": "home-win",
        "algorithm": "ensemble",
        "feature_schema": [{"name": "epa", "data_type": "number"}],
        "artifact": {"uri": f"s3://models/{version}.bin", "digest": _ARTIFACT},
        "registered_by": "trainer",
    }


def _promotion():
    return {
        "policy_id": "policy-main",
        "evaluation_id": "eval-main",
        "evidence_digest": _EVIDENCE,
        "passed": True,
        "evaluated_at": 102.0,
    }


def _candidate(store):
    registered = store.register(_model(), registered_at=100.0)["model_version"]
    return store.transition(
        registered["model_version_id"],
        "candidate",
        occurred_at=101.0,
        actor="trainer",
        reason="ready for evaluation",
    )


def _approved_transition(store, candidate):
    request = store.request_approval(
        {
            "action": "model.lifecycle.transition",
            "target_status": "champion",
            "resource_id": candidate["model_version_id"],
            "evidence_digest": _EVIDENCE,
            "requested_by": "release-manager",
            "reason": "promote passing challenger",
            "expires_at": 200.0,
        },
        requested_at=103.0,
    )["approval"]
    return store.decide_approval(
        request["approval_id"],
        "approved",
        decided_by="risk-owner",
        reason="evaluation evidence verified",
        decided_at=104.0,
    )


def test_manifest_completes_v43_operations_scope():
    manifest = operations_manifest()
    assert manifest["version"] == "4.3.3"
    assert manifest["features"]["persistent_registry_adapters"] is True
    assert manifest["features"]["four_eyes_approvals"] is True
    assert manifest["features"]["automatic_deployment"] is False
    assert manifest["next_increment"] is None


def test_registry_registration_is_idempotent_and_conflict_safe():
    store = InMemoryLifecycleOperations()
    first = store.register(_model(), registered_at=100.0)
    second = store.register(_model(), registered_at=200.0)
    assert first["accepted"] is True
    assert second["deduplicated"] is True
    assert second["model_version"]["registered_at"] == 100.0
    with pytest.raises(ValueError, match="conflict"):
        store.register({**_model(), "algorithm": "different"}, registered_at=100.0)


def test_registry_lists_and_filters_persisted_versions():
    store = InMemoryLifecycleOperations()
    store.register(_model("v8"), registered_at=100.0)
    store.register(_model("v9"), registered_at=101.0)
    assert [item["version"] for item in store.list_versions()] == ["v9", "v8"]
    assert len(store.list_versions(status="registered")) == 2
    assert store.list_versions(model_key="other") == []


def test_low_risk_candidate_transition_does_not_require_approval():
    store = InMemoryLifecycleOperations()
    candidate = _candidate(store)
    assert candidate["status"] == "candidate"
    assert store.audit_history(resource_id=candidate["model_version_id"])[0]["action"] == (
        "model.lifecycle.transitioned"
    )


def test_controlled_transition_requires_exact_approved_evidence():
    store = InMemoryLifecycleOperations()
    candidate = _candidate(store)
    with pytest.raises(ValueError, match="requires an approval"):
        store.transition(
            candidate["model_version_id"],
            "champion",
            occurred_at=105.0,
            actor="release-manager",
            reason="promote",
            promotion_decision=_promotion(),
        )
    approval = _approved_transition(store, candidate)
    promoted = store.transition(
        candidate["model_version_id"],
        "champion",
        occurred_at=105.0,
        actor="release-manager",
        reason="approved promotion",
        promotion_decision=_promotion(),
        approval_id=approval["approval_id"],
    )
    assert promoted["status"] == "champion"
    assert promoted["promotion"]["evidence_digest"] == f"sha256:{_EVIDENCE}"


def test_transition_approval_rejects_mismatched_evidence():
    store = InMemoryLifecycleOperations()
    candidate = _candidate(store)
    approval = store.request_approval(
        {
            "action": "model.lifecycle.transition",
            "target_status": "champion",
            "resource_id": candidate["model_version_id"],
            "evidence_digest": "f" * 64,
            "requested_by": "release-manager",
            "reason": "wrong evidence",
            "expires_at": 200.0,
        },
        requested_at=103.0,
    )["approval"]
    decided = store.decide_approval(
        approval["approval_id"],
        "approved",
        decided_by="risk-owner",
        reason="reviewed",
        decided_at=104.0,
    )
    with pytest.raises(ValueError, match="evidence does not match"):
        store.transition(
            candidate["model_version_id"],
            "champion",
            occurred_at=105.0,
            actor="release-manager",
            reason="promote",
            promotion_decision=_promotion(),
            approval_id=decided["approval_id"],
        )


def test_transition_approval_rejects_mismatched_target_status():
    store = InMemoryLifecycleOperations()
    candidate = _candidate(store)
    approval = store.request_approval(
        {
            "action": "model.lifecycle.transition",
            "target_status": "retired",
            "resource_id": candidate["model_version_id"],
            "evidence_digest": _EVIDENCE,
            "requested_by": "release-manager",
            "reason": "retire candidate",
            "expires_at": 200.0,
        },
        requested_at=103.0,
    )["approval"]
    decided = store.decide_approval(
        approval["approval_id"],
        "approved",
        decided_by="risk-owner",
        reason="reviewed",
        decided_at=104.0,
    )
    with pytest.raises(ValueError, match="target_status does not match"):
        store.transition(
            candidate["model_version_id"],
            "champion",
            occurred_at=105.0,
            actor="release-manager",
            reason="wrong target",
            promotion_decision=_promotion(),
            approval_id=decided["approval_id"],
        )


def test_approval_is_deterministic_bounded_and_four_eyes():
    payload = {
        "action": "model.lifecycle.transition",
        "target_status": "champion",
        "resource_id": "mv_1234567890abcdef1234",
        "evidence_digest": _EVIDENCE,
        "requested_by": "operator-a",
        "reason": "promotion",
        "expires_at": 200.0,
    }
    first = normalize_approval_request(payload, requested_at=100.0)
    second = normalize_approval_request(payload, requested_at=100.0)
    assert first == second
    with pytest.raises(ValueError, match="differ"):
        decide_approval(
            first,
            "approved",
            decided_by="operator-a",
            reason="self approved",
            decided_at=101.0,
        )


def test_approval_expiry_and_unknown_actions_fail_visibly():
    with pytest.raises(ValueError, match="not approval-controlled"):
        normalize_approval_request(
            {
                "action": "shell.execute",
                "resource_id": "mv_1234567890abcdef1234",
                "evidence_digest": _EVIDENCE,
                "requested_by": "operator",
                "reason": "unsafe",
                "expires_at": 200.0,
            },
            requested_at=100.0,
        )
    with pytest.raises(ValueError, match="7 days"):
        normalize_approval_request(
            {
                "action": "model.lifecycle.transition",
                "target_status": "champion",
                "resource_id": "mv_1234567890abcdef1234",
                "evidence_digest": _EVIDENCE,
                "requested_by": "operator",
                "reason": "too long",
                "expires_at": 1_000_000.0,
            },
            requested_at=100.0,
        )


def test_health_observation_emits_alerts_for_breach_and_staleness():
    result = normalize_health_observation(
        {
            "model_version_id": "mv_1234567890abcdef1234",
            "checks": [
                {
                    "name": "quality",
                    "healthy": False,
                    "severity": "critical",
                    "observed_at": 99.0,
                    "evidence_digest": _EVIDENCE,
                    "detail": "quality below threshold",
                },
                {
                    "name": "freshness",
                    "healthy": True,
                    "severity": "warning",
                    "observed_at": 10.0,
                    "maximum_age_seconds": 20,
                    "evidence_digest": _EVIDENCE,
                    "detail": "observation stale",
                },
            ],
        },
        observed_at=100.0,
    )
    assert result["healthy"] is False
    assert {item["reason"] for item in result["alerts"]} == {
        "stale",
        "threshold-breach",
    }


def test_recorded_health_updates_snapshot_and_audit():
    store = InMemoryLifecycleOperations()
    record = store.register(_model(), registered_at=100.0)["model_version"]
    result = store.record_health(
        {
            "model_version_id": record["model_version_id"],
            "checks": [
                {
                    "name": "quality",
                    "healthy": True,
                    "severity": "critical",
                    "observed_at": 101.0,
                    "evidence_digest": _EVIDENCE,
                    "detail": "passing",
                }
            ],
        },
        observed_at=101.0,
        actor="observer",
    )
    snapshot = store.operations_snapshot()
    assert result["healthy"] is True
    assert snapshot["health"]["observed_models"] == 1
    assert snapshot["audit_events"] == 2


def test_audit_history_is_append_only_and_payload_safe():
    store = InMemoryLifecycleOperations()
    candidate = _candidate(store)
    events = store.audit_history(resource_id=candidate["model_version_id"])
    assert [item["sequence"] for item in reversed(events)] == [1, 2]
    assert events[0]["audit_id"].startswith("audit_")
    assert "feature_schema" not in events[0]["details"]


def test_factory_uses_memory_only_when_redis_is_unconfigured(monkeypatch):
    monkeypatch.delenv("REDIS_URL", raising=False)
    assert build_lifecycle_operations(redis_url=None).backend == "memory"
    with pytest.raises(RuntimeError, match="REDIS_URL"):
        build_lifecycle_operations(redis_url=None, allow_memory_fallback=False)


def test_approval_transition_digest_uses_promotion_evidence():
    store = InMemoryLifecycleOperations()
    candidate = _candidate(store)
    assert approval_evidence_for_transition(candidate, "champion", _promotion()) == (f"sha256:{_EVIDENCE}")
    assert approval_evidence_for_transition(candidate, "retired") == candidate["metadata_digest"]


def test_existing_lifecycle_contract_still_rejects_skipped_promotion():
    store = InMemoryLifecycleOperations()
    registered = store.register(_model(), registered_at=100.0)["model_version"]
    with pytest.raises(ValueError, match="cannot transition"):
        transition_model_version(
            registered,
            "champion",
            occurred_at=101.0,
            actor="operator",
            reason="skip",
        )
