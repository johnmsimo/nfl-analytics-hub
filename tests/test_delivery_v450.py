from decision_delivery_v450 import DeliveryConflictError, InMemoryDeliveryBackend, MAX_PAYLOAD_BYTES


def test_delivery_is_idempotent_and_replay_does_not_duplicate():
    backend = InMemoryDeliveryBackend()
    first = backend.enqueue(
        "org_one", "key_one", "request_one", "decision.created", "https://example.com/nfl",
        {"decision_id": "decision_one", "probability": 0.62}, now=100,
    )
    replay = backend.enqueue(
        "org_one", "key_one", "request_one", "decision.created", "https://example.com/nfl",
        {"probability": 0.62, "decision_id": "decision_one"}, now=101,
    )
    assert first["delivery_id"] == replay["delivery_id"]
    assert replay["replayed"] is True
    assert len(backend.list("org_one")) == 1


def test_delivery_idempotency_conflict_fails_closed():
    backend = InMemoryDeliveryBackend()
    backend.enqueue("org_one", "key_one", "request_one", "decision.created", "https://example.com/nfl", {"decision_id": "one"}, now=100)
    try:
        backend.enqueue("org_one", "key_one", "request_one", "decision.created", "https://example.com/nfl", {"decision_id": "different"}, now=101)
    except DeliveryConflictError:
        pass
    else:
        raise AssertionError("different content must conflict")


def test_delivery_is_organization_scoped():
    backend = InMemoryDeliveryBackend()
    created = backend.enqueue("org_one", "key_one", "request_one", "decision.created", "https://example.com/nfl", {}, now=100)
    assert backend.get("org_two", created["delivery_id"]) is None
    assert backend.list("org_two") == []


def test_delivery_validation_rejects_non_https_and_oversized_payloads():
    backend = InMemoryDeliveryBackend()
    for destination in ("http://example.com", "https://user:pass@example.com/hook"):
        try:
            backend.enqueue("org", "key", "request-" + destination, "decision.created", destination, {})
        except ValueError:
            pass
        else:
            raise AssertionError("unsafe destination must be rejected")
    try:
        backend.enqueue("org", "key", "large", "decision.created", "https://example.com/hook", {"blob": "x" * MAX_PAYLOAD_BYTES})
    except ValueError:
        pass
    else:
        raise AssertionError("oversized payload must be rejected")
