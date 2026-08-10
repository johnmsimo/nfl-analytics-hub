import hashlib
import hmac
import json
from unittest.mock import Mock

import requests

from decision_delivery_v450 import delivery_manifest
from delivery_worker import DeliveryWorker


class FakeRedis:
    def __init__(self):
        self.values = {}
        self.retry = {}

    def get(self, key):
        return self.values.get(key)

    def set(self, key, value, ex=None):
        del ex
        self.values[key] = value
        return True

    def zadd(self, key, mapping):
        del key
        self.retry.update(mapping)


def record():
    return {
        "delivery_id": "delivery_test",
        "event_type": "decision.created",
        "destination": "https://example.com/hook",
        "payload": {"decision_id": "d1", "probability": 0.62},
        "status": "queued",
        "attempts": 0,
        "created_at": 100,
        "updated_at": 100,
    }


def worker(transport, now=lambda: 100.0):
    client = FakeRedis()
    result = DeliveryWorker(
        client,
        transport=transport,
        clock=now,
        sleeper=lambda _: None,
        consumer="test-consumer",
    )
    return result, client


def test_success_is_signed_and_recorded():
    transport = Mock()
    transport.post.return_value.status_code = 204
    dispatcher, client = worker(transport)
    result = dispatcher.process_record(record())

    assert result["status"] == "delivered"
    assert result["attempts"] == 1
    kwargs = transport.post.call_args.kwargs
    assert kwargs["data"] == b'{"decision_id":"d1","probability":0.62}'
    timestamp = kwargs["headers"]["X-NFL-Delivery-Timestamp"]
    body = json.dumps(result["payload"], sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    expected = hmac.new(
        b"dev-only-delivery-secret",
        f"{timestamp}.{body}".encode(),
        hashlib.sha256,
    ).hexdigest()
    assert kwargs["headers"]["X-NFL-Delivery-Signature"] == "sha256=" + expected
    assert json.loads(client.values["nfl:v450:delivery:status:delivery_test"])["status"] == "delivered"


def test_retryable_http_failure_is_bounded_and_scheduled(monkeypatch):
    monkeypatch.setenv("V45_DELIVERY_MAX_ATTEMPTS", "2")
    transport = Mock()
    transport.post.return_value.status_code = 503
    dispatcher, client = worker(transport)
    first = dispatcher.process_record(record())

    assert first["status"] == "retrying"
    assert first["attempts"] == 1
    assert client.retry["delivery_test"] == 105

    second = dispatcher.process_record(first)
    assert second["status"] == "failed"
    assert second["attempts"] == 2
    assert transport.post.call_count == 2


def test_request_exception_retries_then_delivers():
    transport = Mock()
    transport.post.side_effect = [requests.Timeout("timed out"), Mock(status_code=200)]
    dispatcher, client = worker(transport)
    first = dispatcher.process_record(record())
    first_status = first["status"]
    second = dispatcher.process_record(first)

    assert first_status == "retrying"
    assert second["status"] == "delivered"
    assert second["last_error"] is None
    assert client.retry["delivery_test"] == 105


def test_non_retryable_http_failure_does_not_schedule():
    transport = Mock()
    transport.post.return_value.status_code = 400
    dispatcher, client = worker(transport)
    result = dispatcher.process_record(record())

    assert result["status"] == "failed"
    assert result["response_status"] == 400
    assert client.retry == {}


def test_retry_stream_fields_accept_compact_delivery_id():
    dispatcher, _ = worker(Mock())
    assert dispatcher._message_delivery_id({"delivery": "delivery_test"}) == "delivery_test"


def test_capabilities_advertise_v451_dispatch_contract():
    manifest = delivery_manifest()
    assert manifest["version"] == "4.5.1"
    assert manifest["intake_version"] == "4.5.0"
    assert manifest["outbound_delivery_enabled"] is True
    assert manifest["signed_dispatch_worker"] is True
