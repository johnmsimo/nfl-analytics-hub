#!/usr/bin/env python3
"""Run the sanitized, strictly read-only P3.8 learning verification."""

from __future__ import annotations

import json
from typing import Any

from database import db
import p38_learning


def _synthetic_receipts(*, samples: int = 60, probability: float = 0.80, wins: int = 18):
    rows = []
    for idx in range(samples):
        grade = "win" if idx < wins else "loss"
        rows.append(
            {
                "grade": grade,
                "release": {
                    "marketKey": "pass_yds" if idx % 2 == 0 else "rec_yds",
                    "decisionGrade": "Play",
                    "confidenceGrade": "A",
                    "side": "over",
                    "consensusProb": probability,
                },
                "result": {
                    "probability": probability,
                    "unitProfit": 0.91 if grade == "win" else -1.0,
                },
            }
        )
    return rows


def _metric_bounds(report: dict[str, Any]) -> bool:
    groups = [report.get("overall") or {}]
    for family in (report.get("segments") or {}).values():
        groups.extend(family.values())
    for metrics in groups:
        for key in ("avgProbability", "hitRate", "brier", "ece"):
            value = metrics.get(key)
            if value is not None and not 0.0 <= float(value) <= 1.0:
                return False
        gap = metrics.get("calibrationGap")
        if gap is not None and not -1.0 <= float(gap) <= 1.0:
            return False
    return True


def _segment_integrity(report: dict[str, Any]) -> bool:
    overall_samples = int((report.get("overall") or {}).get("samples") or 0)
    for family in (report.get("segments") or {}).values():
        if sum(int(row.get("samples") or 0) for row in family.values()) != overall_samples:
            return False
    return True


def main() -> int:
    from app import app

    with app.app_context():
        production = p38_learning.build_learning_report()
        empty = p38_learning.build_report_from_receipts(
            [], min_samples=10, min_segment_samples=5
        )
        synthetic = p38_learning.build_report_from_receipts(
            _synthetic_receipts(),
            min_samples=50,
            min_segment_samples=20,
            calibration_alert=0.05,
            max_ece=0.08,
        )
        db.session.rollback()

    checks = {
        "learning_report_available": production.get("available") is True,
        "read_only_contract": production.get("autoApply") is False
        and (production.get("promotionGate") or {}).get("automaticApply") is False,
        "zero_sample_safe": empty.get("state") == "collecting"
        and empty.get("gradedCalibrationSamples") == 0
        and empty.get("autoApply") is False,
        "metric_bounds": _metric_bounds(production),
        "segment_integrity": _segment_integrity(production),
        "synthetic_overconfidence_detected": any(
            signal.get("type") == "overconfidence"
            for signal in synthetic.get("signals", [])
        ),
        "synthetic_never_auto_applies": synthetic.get("autoApply") is False
        and synthetic.get("promotionGate", {}).get("automaticApply") is False,
    }
    blockers = [name for name, passed in checks.items() if not passed]
    payload = {
        "ok": not blockers,
        "phase": "P3.8",
        "mode": "read-only",
        "blockingFailures": blockers,
        "gates": checks,
        "learning": production,
    }
    print(json.dumps(payload, sort_keys=True))
    return 0 if not blockers else 1


if __name__ == "__main__":
    raise SystemExit(main())
