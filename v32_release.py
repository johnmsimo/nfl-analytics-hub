"""Completion services for NFL Analytics Hub v3.2.

The module is dependency-light and keeps public contracts deterministic. Profile
persistence is file-backed with atomic writes so it works on SQLite, PostgreSQL,
and local development without coupling preferences to the warehouse schema.
"""
from __future__ import annotations

import json
import math
import os
import tempfile
import threading
import time
from collections import defaultdict, deque
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any


class ProfileStore:
    """Thread-safe JSON profile persistence keyed by authenticated subject."""

    def __init__(self, path: str | Path | None = None) -> None:
        default = Path(os.environ.get("DATA_DIR", "data")) / "v32_profiles.json"
        self.path = Path(path or os.environ.get("V32_PROFILE_STORE", default))
        self._lock = threading.RLock()

    @staticmethod
    def _subject(value: Any) -> str:
        subject = str(value or "").strip()
        if not subject or len(subject) > 128:
            raise ValueError("a valid authenticated subject is required")
        return subject

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def get(self, subject: Any) -> dict[str, Any]:
        key = self._subject(subject)
        with self._lock:
            value = self._read().get(key, {})
        return dict(value) if isinstance(value, dict) else {}

    def put(self, subject: Any, profile: Mapping[str, Any]) -> dict[str, Any]:
        key = self._subject(subject)
        stored = dict(profile)
        stored["subject"] = key
        stored["version"] = "3.2"
        stored["updated_at"] = int(time.time())
        with self._lock:
            data = self._read()
            data[key] = stored
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_name = tempfile.mkstemp(prefix="v32-profile-", suffix=".json", dir=self.path.parent)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(data, handle, separators=(",", ":"), sort_keys=True)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(tmp_name, self.path)
            finally:
                if os.path.exists(tmp_name):
                    os.unlink(tmp_name)
        return dict(stored)


class MetricsRegistry:
    """Small in-process metrics registry for endpoint and provider health."""

    def __init__(self, sample_limit: int = 500) -> None:
        self._lock = threading.RLock()
        self._counts: dict[str, int] = defaultdict(int)
        self._latencies: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=sample_limit))
        self._freshness: dict[str, float] = {}

    def observe(self, name: str, latency_ms: float, success: bool = True) -> None:
        with self._lock:
            self._counts[f"{name}.requests"] += 1
            self._counts[f"{name}.{'success' if success else 'error'}"] += 1
            self._latencies[name].append(max(0.0, float(latency_ms)))

    def mark_fresh(self, provider: str, observed_at: float | None = None) -> None:
        with self._lock:
            self._freshness[str(provider)] = float(observed_at or time.time())

    def snapshot(self) -> dict[str, Any]:
        now = time.time()
        with self._lock:
            latency = {}
            for name, samples in self._latencies.items():
                ordered = sorted(samples)
                if not ordered:
                    continue
                index = max(0, math.ceil(len(ordered) * 0.95) - 1)
                latency[name] = {
                    "count": len(ordered),
                    "avg_ms": round(sum(ordered) / len(ordered), 2),
                    "p95_ms": round(ordered[index], 2),
                }
            freshness = {
                provider: {"age_seconds": round(max(0.0, now - stamp), 1), "observed_at": int(stamp)}
                for provider, stamp in self._freshness.items()
            }
            return {"counts": dict(self._counts), "latency": latency, "provider_freshness": freshness}


PROFILE_STORE = ProfileStore()
METRICS = MetricsRegistry()


def _probability(value: Any) -> float:
    number = float(value)
    if number > 1:
        number /= 100.0
    return min(max(number, 0.0), 1.0)


def calibration_report(predictions: Sequence[Mapping[str, Any]], bins: int = 10) -> dict[str, Any]:
    """Calculate Brier score, log loss, accuracy, and reliability buckets."""
    bins = min(max(int(bins), 2), 20)
    rows: list[tuple[float, int]] = []
    for item in predictions:
        try:
            probability = _probability(item.get("probability"))
            outcome = 1 if bool(item.get("outcome")) else 0
        except (TypeError, ValueError):
            continue
        rows.append((probability, outcome))
    if not rows:
        return {"count": 0, "brier_score": None, "log_loss": None, "accuracy": None, "buckets": []}

    epsilon = 1e-12
    brier = sum((p - y) ** 2 for p, y in rows) / len(rows)
    log_loss = -sum(y * math.log(max(p, epsilon)) + (1 - y) * math.log(max(1 - p, epsilon)) for p, y in rows) / len(rows)
    accuracy = sum((p >= 0.5) == bool(y) for p, y in rows) / len(rows)
    buckets = []
    for index in range(bins):
        low, high = index / bins, (index + 1) / bins
        selected = [(p, y) for p, y in rows if low <= p < high or (index == bins - 1 and p == 1)]
        if selected:
            buckets.append({
                "range": [round(low, 3), round(high, 3)],
                "count": len(selected),
                "mean_probability": round(sum(p for p, _ in selected) / len(selected), 4),
                "observed_rate": round(sum(y for _, y in selected) / len(selected), 4),
            })
    return {
        "count": len(rows),
        "brier_score": round(brier, 6),
        "log_loss": round(log_loss, 6),
        "accuracy": round(accuracy, 6),
        "buckets": buckets,
    }


def backtest_report(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Summarize chronological model performance and optional betting ROI."""
    valid = []
    for item in records:
        try:
            probability = _probability(item.get("probability"))
            outcome = 1 if bool(item.get("outcome")) else 0
            stake = max(0.0, float(item.get("stake", 0)))
            profit = float(item.get("profit", 0))
        except (TypeError, ValueError):
            continue
        valid.append((probability, outcome, stake, profit))
    calibration = calibration_report(
        [{"probability": p, "outcome": y} for p, y, _, _ in valid]
    )
    total_stake = sum(stake for _, _, stake, _ in valid)
    total_profit = sum(profit for _, _, _, profit in valid)
    return {
        "count": len(valid),
        "calibration": calibration,
        "total_stake": round(total_stake, 2),
        "total_profit": round(total_profit, 2),
        "roi": round(total_profit / total_stake, 6) if total_stake else None,
    }


def drift_report(reference: Sequence[Any], current: Sequence[Any]) -> dict[str, Any]:
    """Return standardized mean drift and a simple severity classification."""
    try:
        ref = [float(value) for value in reference]
        cur = [float(value) for value in current]
    except (TypeError, ValueError):
        ref, cur = [], []
    if len(ref) < 2 or len(cur) < 2:
        return {"status": "insufficient_data", "score": None, "severity": "unknown"}
    ref_mean = sum(ref) / len(ref)
    cur_mean = sum(cur) / len(cur)
    variance = sum((value - ref_mean) ** 2 for value in ref) / (len(ref) - 1)
    scale = math.sqrt(variance)
    score = abs(cur_mean - ref_mean) / scale if scale else (0.0 if cur_mean == ref_mean else float("inf"))
    severity = "low" if score < 0.5 else "moderate" if score < 1.0 else "high"
    return {
        "status": "ok",
        "score": round(score, 6) if math.isfinite(score) else None,
        "severity": severity,
        "reference_mean": round(ref_mean, 6),
        "current_mean": round(cur_mean, 6),
    }


def generated_report(kind: str, context: Mapping[str, Any]) -> dict[str, Any]:
    """Generate grounded preview, recap, or weekly-report copy from supplied facts."""
    report_kind = str(kind or "preview").strip().lower()
    if report_kind not in {"preview", "recap", "weekly"}:
        raise ValueError("kind must be preview, recap, or weekly")
    title = str(context.get("title") or context.get("matchup") or "NFL Intelligence Report")
    factors = context.get("key_factors") or context.get("highlights") or []
    if not isinstance(factors, list):
        factors = []
    summary = str(context.get("summary") or "No narrative summary was supplied.")
    confidence = context.get("confidence")
    lines = [summary]
    if factors:
        lines.append("Key factors: " + "; ".join(str(item) for item in factors[:5]) + ".")
    if confidence is not None:
        lines.append(f"Model confidence: {confidence}.")
    return {
        "kind": report_kind,
        "title": title,
        "body": " ".join(lines),
        "grounded": True,
        "source_fields": sorted(str(key) for key in context.keys()),
        "version": "3.2",
    }
