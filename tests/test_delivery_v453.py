from delivery_observability_v453 import delivery_health
from decision_delivery_v450 import InMemoryDeliveryBackend


class FakeRedis:
    def __init__(self, heartbeat):
        self.heartbeat = heartbeat

    def ping(self):
        return True

    def xlen(self, _stream):
        return 12

    def xpending(self, _stream, _group):
        return {"pending": 2}

    def zcard(self, _retry):
        return 3

    def scan_iter(self, match):
        assert match.endswith(":heartbeat:*")
        return ["nfl:v450:delivery:heartbeat:worker-1"]

    def get(self, _key):
        return self.heartbeat


class RedisBackend:
    backend = "redis"
    distributed = True
    key_prefix = "nfl:v450:delivery"

    def __init__(self, heartbeat):
        self.client = FakeRedis(heartbeat)


def test_memory_backend_is_explicitly_not_production_ready():
    result = delivery_health(InMemoryDeliveryBackend(), now=100)
    assert result["status"] == "degraded"
    assert result["reason"] == "in_memory_backend"
    assert result["production_ready"] is False


def test_redis_health_reports_queue_and_active_worker():
    backend = RedisBackend(
        '{"consumer":"worker-1","group":"v451-dispatch","updated_at":95,"ttl_seconds":10}'
    )
    result = delivery_health(backend, now=100)
    assert result["status"] == "ok"
    assert result["queue"]["stream_length"] == 12
    assert result["queue"]["pending_count"] == 2
    assert result["queue"]["retry_backlog"] == 3
    assert result["workers"]["active_count"] == 1


def test_stale_worker_heartbeat_degrades_health():
    backend = RedisBackend(
        '{"consumer":"worker-1","group":"v451-dispatch","updated_at":70,"ttl_seconds":10}'
    )
    result = delivery_health(backend, now=100)
    assert result["status"] == "degraded"
    assert result["reason"] == "worker_heartbeat_stale"
    assert result["workers"]["active_count"] == 0
