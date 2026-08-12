from delivery_observability_v453 import delivery_health
from decision_delivery_v450 import InMemoryDeliveryBackend


class FakeRedis:
    def __init__(self, pending=2, retry=3):
        self.pending = pending
        self.retry = retry

    def ping(self):
        return True

    def xlen(self, _stream):
        return 12

    def xpending(self, _stream, _group):
        return {"pending": self.pending}

    def zcard(self, _retry):
        return self.retry

    def scan_iter(self, match):
        assert match.endswith(":heartbeat:*")
        return ["nfl:v450:delivery:heartbeat:worker-1"]

    def get(self, _key):
        return '{"consumer":"worker-1","group":"v451-dispatch","updated_at":95,"ttl_seconds":10}'


class RedisBackend:
    backend = "redis"
    distributed = True
    key_prefix = "nfl:v450:delivery"

    def __init__(self, pending=2, retry=3):
        self.client = FakeRedis(pending, retry)


def test_memory_backend_is_explicitly_not_production_ready():
    result = delivery_health(InMemoryDeliveryBackend(), now=100)
    assert result["status"] == "degraded"
    assert result["reason"] == "in_memory_backend"
    assert result["production_ready"] is False


def test_v454_reports_backlog_pressure_and_probe_latency():
    result = delivery_health(RedisBackend(pending=60, retry=50), now=100)
    assert result["version"] == "4.5.4"
    assert result["queue"]["total_backlog"] == 110
    assert result["queue"]["pressure"] == "elevated"
    assert result["queue"]["recommendation"] == "investigate_worker_capacity"
    assert result["redis"]["probe_latency_ms"] >= 0
    assert result["status"] == "degraded"
    assert result["reason"] == "delivery_backlog_elevated"


def test_v454_critical_backlog_is_bounded_and_actionable(monkeypatch):
    monkeypatch.setenv("V45_DELIVERY_CRITICAL_BACKLOG", "10")
    result = delivery_health(RedisBackend(pending=8, retry=3), now=100)
    assert result["queue"]["pressure"] == "critical"
    assert result["queue"]["recommendation"] == "scale_delivery_workers_or_pause_new_intake"
    assert result["reason"] == "delivery_backlog_critical"
