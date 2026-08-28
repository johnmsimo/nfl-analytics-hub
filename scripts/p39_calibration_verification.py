#!/usr/bin/env python3
"""Sanitized, strictly read-only P3.9 calibration challenger verification."""
from __future__ import annotations

import json
from typing import Any

from database import db
import p39_calibration


def _receipt(idx: int, probability: float, won: bool) -> dict[str, Any]:
    return {
        "receiptId": f"synthetic-{idx:04d}",
        "releasedAt": f"2026-09-{1 + idx // 24:02d}T{idx % 24:02d}:00:00+00:00",
        "grade": "win" if won else "loss",
        "release": {"consensusProb": probability},
        "result": {"probability": probability},
    }


def _overconfident() -> list[dict[str, Any]]:
    return [_receipt(i, 0.80, i % 2 == 0) for i in range(120)]


def _well_calibrated() -> list[dict[str, Any]]:
    return [_receipt(i, 0.60, (i % 5) < 3) for i in range(120)]


def _metric_bounds(report: dict[str, Any]) -> bool:
    candidate = report.get("candidate")
    if candidate is None:
        return True
    for phase in ("train", "validation"):
        for model in ("champion", "challenger"):
            metrics = candidate.get(phase, {}).get(model, {})
            for key in ("brier", "ece", "avgProbability", "hitRate"):
                value = metrics.get(key)
                if value is not None and not 0.0 <= float(value) <= 1.0:
                    return False
    return True


def main() -> int:
    from app import app

    with app.app_context():
        production = p39_calibration.build_production_report()
        overconfident = p39_calibration.build_candidate_report(
            _overconfident(),
            min_samples=80,
            min_validation_samples=24,
            train_fraction=0.70,
            min_brier_improvement=0.005,
            max_ece_regression=0.01,
        )
        calibrated = p39_calibration.build_candidate_report(
            _well_calibrated(),
            min_samples=80,
            min_validation_samples=24,
            train_fraction=0.70,
            min_brier_improvement=0.005,
            max_ece_regression=0.01,
        )
        db.session.rollback()

    gates = {
        "production_report_available": production.get("available") is True,
        "read_only_contract": production.get("autoApply") is False
        and production.get("productionApplied") is False,
        "production_gate_never_auto_applies": (production.get("promotionGate") or {}).get("automaticApply") is False,
        "metric_bounds": _metric_bounds(production),
        "synthetic_overconfidence_generates_candidate": overconfident.get("candidate") is not None,
        "synthetic_overconfidence_clears_holdout_gate": (overconfident.get("promotionGate") or {}).get("eligible") is True,
        "synthetic_candidate_is_forward_holdout": (overconfident.get("candidate") or {}).get("validationIsForwardHoldout") is True,
        "well_calibrated_data_not_promoted": (calibrated.get("promotionGate") or {}).get("eligible") is False,
        "synthetic_never_auto_applies": overconfident.get("autoApply") is False
        and overconfident.get("productionApplied") is False,
    }
    blockers = [name for name, passed in gates.items() if not passed]
    payload = {
        "ok": not blockers,
        "phase": "P3.9",
        "mode": "read-only",
        "blockingFailures": blockers,
        "gates": gates,
        "calibration": production,
    }
    print(json.dumps(payload, sort_keys=True))
    return 0 if not blockers else 1


if __name__ == "__main__":
    raise SystemExit(main())
