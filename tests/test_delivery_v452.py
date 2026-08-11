from decision_delivery_v450 import InMemoryDeliveryBackend
from delivery_operations_v452 import delivery_metrics, list_dead_letters, replay_delivery


def _queued(backend, *, status="dead_letter", workspace_id="workspace_one"):
    record = backend.enqueue(
        "org_one",
        "key_one",
        "request_one",
        "decision.created",
        "https://example.com/nfl",
        {"decision_id": "one"},
        now=100,
    )
    stored = backend._records[record["delivery_id"]]
    stored.update(
        status=status,
        workspace_id=workspace_id,
        attempts=3,
        updated_at=101,
        last_error="HTTP 503",
    )
    return stored


def test_dead_letter_listing_is_tenant_and_workspace_scoped():
    backend = InMemoryDeliveryBackend()
    record = _queued(backend)
    assert list_dead_letters(backend, "org_one", workspace_id="workspace_one")[0]["delivery_id"] == record["delivery_id"]
    assert list_dead_letters(backend, "org_two") == []
    assert list_dead_letters(backend, "org_one", workspace_id="other") == []


def test_replay_resets_terminal_delivery_and_preserves_identity():
    backend = InMemoryDeliveryBackend()
    record = _queued(backend)
    replay = replay_delivery(
        backend,
        "org_one",
        record["delivery_id"],
        context={"membership_id": "m1"},
        workspace_id="workspace_one",
    )
    assert replay["delivery_id"] == record["delivery_id"]
    assert replay["status"] == "queued"
    assert replay["attempts"] == 0
    assert replay["replay_count"] == 1


def test_metrics_report_terminal_and_attempt_counts():
    backend = InMemoryDeliveryBackend()
    _queued(backend, status="dead_letter")
    second = backend.enqueue(
        "org_one", "key_one", "request_two", "decision.created", "https://example.com/nfl", {}, now=102
    )
    backend._records[second["delivery_id"]]["status"] = "delivered"
    metrics = delivery_metrics(backend, "org_one")
    assert metrics["delivery_count"] == 2
    assert metrics["dead_letter_count"] == 1
    assert metrics["status_counts"]["delivered"] == 1
    assert metrics["attempt_count"] == 3


def test_workspace_is_part_of_idempotency_content():
    backend = InMemoryDeliveryBackend()
    backend.enqueue(
        "org_one", "key_one", "same-key", "decision.created", "https://example.com/nfl", {},
        workspace_id="workspace_one", now=100,
    )
    try:
        backend.enqueue(
            "org_one", "key_one", "same-key", "decision.created", "https://example.com/nfl", {},
            workspace_id="workspace_two", now=101,
        )
    except ValueError:
        pass
    else:
        raise AssertionError("changing workspace content must conflict")
