"""Dependency-light v3.2 live event broker and search contracts."""
from __future__ import annotations

import threading
import time
import uuid
from collections import deque
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

_ALLOWED_TOPICS = {"scores", "odds", "injuries", "models", "system"}
_ALLOWED_ENTITY_TYPES = {"game", "team", "player", "prop"}


@dataclass(frozen=True)
class LiveEvent:
    event_id: str
    topic: str
    event: str
    data: dict[str, Any]
    created_at: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.event_id,
            "topic": self.topic,
            "event": self.event,
            "data": self.data,
            "created_at": self.created_at,
        }


class EventBroker:
    """Small bounded broker suitable for one-process SSE deployments.

    Redis/pub-sub can replace this implementation later without changing the route contract.
    """

    def __init__(self, max_events: int = 500) -> None:
        self._events: deque[LiveEvent] = deque(maxlen=max_events)
        self._condition = threading.Condition()

    def publish(self, topic: str, event: str, data: Mapping[str, Any]) -> LiveEvent:
        normalized_topic = str(topic).strip().lower()
        if normalized_topic not in _ALLOWED_TOPICS:
            raise ValueError(f"unsupported topic: {normalized_topic}")
        normalized_event = str(event).strip().lower().replace(" ", "_")
        if not normalized_event:
            raise ValueError("event is required")
        item = LiveEvent(
            event_id=uuid.uuid4().hex,
            topic=normalized_topic,
            event=normalized_event,
            data=dict(data),
            created_at=time.time(),
        )
        with self._condition:
            self._events.append(item)
            self._condition.notify_all()
        return item

    def events_after(self, event_id: str | None, topics: set[str] | None = None) -> list[LiveEvent]:
        with self._condition:
            snapshot = list(self._events)
        start = 0
        if event_id:
            for index, item in enumerate(snapshot):
                if item.event_id == event_id:
                    start = index + 1
                    break
        return [item for item in snapshot[start:] if not topics or item.topic in topics]

    def wait(self, timeout: float) -> None:
        with self._condition:
            self._condition.wait(timeout=max(0.0, timeout))


BROKER = EventBroker()


def normalize_topics(values: Iterable[str] | str | None) -> set[str]:
    if values is None:
        return set(_ALLOWED_TOPICS)
    if isinstance(values, str):
        values = values.split(",")
    topics = {str(value).strip().lower() for value in values}
    return topics & _ALLOWED_TOPICS or set(_ALLOWED_TOPICS)


def normalize_saved_filter(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    data = dict(payload or {})
    name = str(data.get("name", "My filter")).strip()[:80] or "My filter"
    entity_types = data.get("entity_types", list(_ALLOWED_ENTITY_TYPES))
    if not isinstance(entity_types, (list, tuple, set)):
        entity_types = list(_ALLOWED_ENTITY_TYPES)
    normalized_types = [
        item for item in dict.fromkeys(str(value).strip().lower() for value in entity_types)
        if item in _ALLOWED_ENTITY_TYPES
    ] or sorted(_ALLOWED_ENTITY_TYPES)
    query = str(data.get("query", "")).strip()[:120]
    return {
        "id": str(data.get("id") or uuid.uuid4().hex),
        "name": name,
        "query": query,
        "entity_types": normalized_types,
        "favorites_only": bool(data.get("favorites_only", False)),
        "live_only": bool(data.get("live_only", False)),
        "version": "3.2",
    }


def search_entities(
    query: str,
    entities: Iterable[Mapping[str, Any]],
    entity_types: Iterable[str] | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    needle = str(query).strip().lower()
    allowed = normalize_saved_filter({"entity_types": list(entity_types or _ALLOWED_ENTITY_TYPES)})[
        "entity_types"
    ]
    capped_limit = min(max(int(limit), 1), 100)
    matches: list[tuple[int, dict[str, Any]]] = []
    for raw in entities:
        item = dict(raw)
        entity_type = str(item.get("type", "")).lower()
        if entity_type not in allowed:
            continue
        searchable = " ".join(
            str(item.get(key, "")) for key in ("name", "title", "team", "opponent", "position")
        ).lower()
        if needle and needle not in searchable:
            continue
        score = 2 if searchable.startswith(needle) and needle else 1
        matches.append((score, item))
    matches.sort(key=lambda pair: (-pair[0], str(pair[1].get("name") or pair[1].get("title") or "")))
    return [item for _, item in matches[:capped_limit]]
