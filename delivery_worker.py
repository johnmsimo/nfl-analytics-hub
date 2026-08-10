"""Redis-backed v4.5.1 delivery dispatcher.

The web process only accepts and queues delivery jobs. This module is the
separate process that consumes those jobs, signs the canonical JSON payload,
performs bounded outbound HTTPS delivery, and records the result in the same
Redis status record exposed by the v4.5 API.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import signal
import socket
import time
from collections.abc import Callable, Iterator
from typing import Any

import redis
import requests

STREAM_SUFFIX = ":stream"
GROUP_SUFFIX = ":dispatch-group"
RETRY_SUFFIX = ":retry"
DEFAULT_MAX_ATTEMPTS = 5
MAX_MAX_ATTEMPTS = 10
DEFAULT_BACKOFF_SECONDS = 5
MAX_BACKOFF_SECONDS = 15 * 60
DEFAULT_TIMEOUT_SECONDS = 10
MAX_TIMEOUT_SECONDS = 30
DEFAULT_CLAIM_IDLE_SECONDS = 60
RETRYABLE_STATUS_CODES = frozenset({408, 425, 429}) | frozenset(range(500, 600))
TERMINAL_STATUSES = frozenset({"delivered", "failed", "dead_letter"})


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _env_int(name: str, default: int, low: int, high: int) -> int:
    raw = os.getenv(name)
    try:
        value = default if raw is None else int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer") from exc
    if not low <= value <= high:
        raise RuntimeError(f"{name} must be between {low} and {high}")
    return value


def _text(value: Any) -> str:
    return value.decode("utf-8") if isinstance(value, bytes) else str(value)


class DeliveryWorker:
    """One Redis-stream delivery consumer.

    A single worker process is sufficient for the Fly deployment, while the
    consumer group and stale-claim handling keep the queue safe if that
    process is restarted or later scaled horizontally.
    """

    def __init__(
        self,
        client: Any,
        *,
        transport: Any | None = None,
        clock: Callable[[], float] = time.time,
        sleeper: Callable[[float], None] = time.sleep,
        consumer: str | None = None,
    ) -> None:
        self.client = client
        self.transport = transport or requests.Session()
        self.clock = clock
        self.sleeper = sleeper
        self.prefix = os.getenv("V45_DELIVERY_KEY_PREFIX", "nfl:v450:delivery")
        self.stream = self.prefix + STREAM_SUFFIX
        self.group = os.getenv("V45_DELIVERY_CONSUMER_GROUP", "v451-dispatch")
        self.consumer = consumer or f"{socket.gethostname()}-{os.getpid()}"
        self.max_attempts = _env_int("V45_DELIVERY_MAX_ATTEMPTS", DEFAULT_MAX_ATTEMPTS, 1, MAX_MAX_ATTEMPTS)
        self.backoff_seconds = _env_int(
            "V45_DELIVERY_BACKOFF_SECONDS",
            DEFAULT_BACKOFF_SECONDS,
            1,
            MAX_BACKOFF_SECONDS,
        )
        self.timeout_seconds = _env_int(
            "V45_DELIVERY_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS, 1, MAX_TIMEOUT_SECONDS
        )
        self.claim_idle_seconds = _env_int(
            "V45_DELIVERY_CLAIM_IDLE_SECONDS",
            DEFAULT_CLAIM_IDLE_SECONDS,
            10,
            24 * 60 * 60,
        )
        self.ttl_seconds = _env_int("V45_DELIVERY_TTL_SECONDS", 7 * 24 * 60 * 60, 3600, 30 * 24 * 60 * 60)
        configured_secret = os.getenv("V45_DELIVERY_SIGNING_SECRET")
        if not configured_secret:
            if os.getenv("APP_ENV", os.getenv("FLASK_ENV", "development")).lower() == "production":
                raise RuntimeError("V45_DELIVERY_SIGNING_SECRET is required in production")
            configured_secret = os.getenv("SECRET_KEY", "dev-only-delivery-secret")
        self.signing_secret = configured_secret
        self._stop = False

    def _status_key(self, delivery_id: str) -> str:
        return f"{self.prefix}:status:{delivery_id}"

    @property
    def retry_key(self) -> str:
        return self.prefix + RETRY_SUFFIX

    def ensure_group(self) -> None:
        """Create the group from the beginning so pre-worker queued jobs drain."""
        try:
            self.client.xgroup_create(self.stream, self.group, id="0-0", mkstream=True)
        except redis.exceptions.ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    def _load(self, delivery_id: str) -> dict[str, Any] | None:
        raw = self.client.get(self._status_key(delivery_id))
        if not raw:
            return None
        try:
            result = json.loads(_text(raw))
        except (TypeError, ValueError):
            return None
        return result if isinstance(result, dict) else None

    def _save(self, record: dict[str, Any]) -> None:
        self.client.set(
            self._status_key(record["delivery_id"]),
            _canonical(record),
            ex=self.ttl_seconds,
        )

    def _update(self, record: dict[str, Any], **fields: Any) -> dict[str, Any]:
        record.update(fields)
        record["updated_at"] = round(self.clock(), 6)
        self._save(record)
        return record

    def _signature(self, record: dict[str, Any], timestamp: str) -> str:
        body = _canonical(record.get("payload")).encode("utf-8")
        message = timestamp.encode("ascii") + b"." + body
        digest = hmac.new(self.signing_secret.encode("utf-8"), message, hashlib.sha256).hexdigest()
        return "sha256=" + digest

    def _headers(self, record: dict[str, Any]) -> dict[str, str]:
        timestamp = str(int(self.clock()))
        return {
            "Content-Type": "application/json",
            "User-Agent": "nfl-analytics-hub-v4.5.1",
            "X-NFL-Delivery-ID": str(record["delivery_id"]),
            "X-NFL-Event-Type": str(record["event_type"]),
            "X-NFL-Delivery-Timestamp": timestamp,
            "X-NFL-Delivery-Signature": self._signature(record, timestamp),
        }

    def _error_text(self, error: Any) -> str:
        return str(error).replace("\n", " ")[:500]

    def _retry_delay(self, attempts: int) -> int:
        return min(MAX_BACKOFF_SECONDS, self.backoff_seconds * (2 ** max(0, attempts - 1)))

    def _schedule_retry(self, record: dict[str, Any], attempts: int, error: str, status: int | None) -> None:
        retry_at = round(self.clock() + self._retry_delay(attempts), 6)
        # Schedule before saving the retrying state. A process interruption can
        # then leave a harmless queued record, but cannot lose the retry timer.
        self.client.zadd(self.retry_key, {record["delivery_id"]: retry_at})
        self._update(
            record,
            status="retrying",
            attempts=attempts,
            next_attempt_at=retry_at,
            last_error=error,
            response_status=status,
        )

    def process_record(self, record: dict[str, Any]) -> dict[str, Any]:
        """Attempt one delivery and return its updated status record."""
        if record.get("status") in TERMINAL_STATUSES:
            return record
        attempts = int(record.get("attempts") or 0) + 1
        self._update(record, status="dispatching", attempts=attempts, next_attempt_at=None)
        try:
            body = _canonical(record.get("payload")).encode("utf-8")
            response = self.transport.post(
                record["destination"],
                data=body,
                headers=self._headers(record),
                timeout=self.timeout_seconds,
            )
        except requests.RequestException as exc:
            response_status = None
            error = self._error_text(exc)
            if attempts < self.max_attempts:
                self._schedule_retry(record, attempts, error, response_status)
            else:
                self._update(
                    record,
                    status="failed",
                    last_error=error,
                    response_status=response_status,
                    failed_at=round(self.clock(), 6),
                )
            return record

        response_status = int(response.status_code)
        if 200 <= response_status < 300:
            return self._update(
                record,
                status="delivered",
                response_status=response_status,
                delivered_at=round(self.clock(), 6),
                last_error=None,
                next_attempt_at=None,
            )

        error = f"destination returned HTTP {response_status}"
        if response_status in RETRYABLE_STATUS_CODES and attempts < self.max_attempts:
            self._schedule_retry(record, attempts, error, response_status)
        else:
            self._update(
                record,
                status="failed",
                response_status=response_status,
                last_error=error,
                failed_at=round(self.clock(), 6),
                next_attempt_at=None,
            )
        return record

    def _message_delivery_id(self, fields: dict[Any, Any]) -> str | None:
        value = fields.get("delivery") or fields.get(b"delivery")
        if value is None:
            return None
        try:
            parsed = json.loads(_text(value))
            if isinstance(parsed, dict):
                return str(parsed["delivery_id"])
            return str(parsed)
        except (KeyError, TypeError, ValueError):
            # Retry scheduling stores only the delivery id to keep the sorted
            # set small; initial intake stores the full JSON record.
            raw = _text(value).strip()
            return raw or None

    def _ack(self, message_id: Any) -> None:
        self.client.xack(self.stream, self.group, message_id)

    def _process_message(self, message_id: Any, fields: dict[Any, Any]) -> None:
        delivery_id = self._message_delivery_id(fields)
        if delivery_id:
            record = self._load(delivery_id)
            if record is not None:
                self.process_record(record)
        self._ack(message_id)

    def _messages(self, result: Any) -> Iterator[tuple[Any, dict[Any, Any]]]:
        for stream, entries in result or []:
            del stream
            yield from entries

    def _reclaim_pending(self) -> list[tuple[Any, dict[Any, Any]]]:
        try:
            result = self.client.xautoclaim(
                self.stream,
                self.group,
                self.consumer,
                self.claim_idle_seconds * 1000,
                "0-0",
                count=10,
            )
        except (AttributeError, redis.exceptions.ResponseError):
            return []
        return list(self._messages([(self.stream, result[1])])) if result else []

    def _enqueue_due_retries(self) -> None:
        now = self.clock()
        due = self.client.zrangebyscore(self.retry_key, 0, now, start=0, num=10)
        for value in due:
            delivery_id = _text(value)
            if not self.client.zrem(self.retry_key, delivery_id):
                continue
            self.client.xadd(self.stream, {"delivery": delivery_id}, maxlen=10000, approximate=True)

    def run_once(self, *, block_ms: int = 1000) -> int:
        """Process stale, due, and newly queued jobs once; return a count."""
        processed = 0
        self._enqueue_due_retries()
        for message_id, fields in self._reclaim_pending():
            self._process_message(message_id, fields)
            processed += 1
        result = self.client.xreadgroup(
            self.group,
            self.consumer,
            {self.stream: ">"},
            count=10,
            block=block_ms,
        )
        for message_id, fields in self._messages(result):
            self._process_message(message_id, fields)
            processed += 1
        return processed

    def stop(self, *_signals: Any) -> None:
        self._stop = True

    def run_forever(self) -> None:
        self.ensure_group()
        while not self._stop:
            self.run_once()


def main() -> None:
    redis_url = os.getenv("REDIS_URL")
    if not redis_url:
        raise SystemExit("REDIS_URL is required for the v4.5.1 dispatch worker")
    client = redis.Redis.from_url(
        redis_url,
        decode_responses=True,
        socket_connect_timeout=3,
        socket_timeout=30,
    )
    client.ping()
    worker = DeliveryWorker(client)
    signal.signal(signal.SIGTERM, worker.stop)
    signal.signal(signal.SIGINT, worker.stop)
    worker.run_forever()


if __name__ == "__main__":
    main()
