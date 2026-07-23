from pathlib import Path

from v32_release import (
    MetricsRegistry,
    ProfileStore,
    backtest_report,
    calibration_report,
    drift_report,
    generated_report,
)


def test_profile_store_round_trip(tmp_path: Path):
    store = ProfileStore(tmp_path / "profiles.json")
    saved = store.put("john", {"preferences": {"density": "compact"}})
    assert saved["subject"] == "john"
    assert store.get("john")["preferences"]["density"] == "compact"


def test_calibration_report_is_deterministic():
    report = calibration_report(
        [
            {"probability": 0.8, "outcome": True},
            {"probability": 0.3, "outcome": False},
            {"probability": 0.6, "outcome": True},
        ],
        bins=5,
    )
    assert report["count"] == 3
    assert report["brier_score"] == 0.096667
    assert report["accuracy"] == 1.0
    assert report["buckets"]


def test_backtest_reports_roi():
    report = backtest_report(
        [
            {"probability": 0.7, "outcome": True, "stake": 10, "profit": 8},
            {"probability": 0.4, "outcome": False, "stake": 10, "profit": -10},
        ]
    )
    assert report["total_stake"] == 20
    assert report["total_profit"] == -2
    assert report["roi"] == -0.1


def test_drift_severity():
    low = drift_report([1, 2, 3, 4], [1.1, 2.1, 3.1, 4.1])
    high = drift_report([1, 2, 3, 4], [10, 11, 12, 13])
    assert low["severity"] == "low"
    assert high["severity"] == "high"


def test_metrics_registry_tracks_latency_and_freshness():
    metrics = MetricsRegistry()
    metrics.observe("search", 10)
    metrics.observe("search", 30, success=False)
    metrics.mark_fresh("scores", 100)
    snapshot = metrics.snapshot()
    assert snapshot["counts"]["search.requests"] == 2
    assert snapshot["latency"]["search"]["avg_ms"] == 20
    assert "scores" in snapshot["provider_freshness"]


def test_generated_report_is_grounded():
    report = generated_report(
        "preview",
        {
            "title": "BUF at MIA",
            "summary": "Buffalo has the stronger passing profile.",
            "key_factors": ["EPA advantage", "healthy offensive line"],
            "confidence": 72,
        },
    )
    assert report["grounded"] is True
    assert "EPA advantage" in report["body"]
    assert report["kind"] == "preview"
