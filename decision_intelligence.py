"""P3.4 simulation-confirmed player-prop decision intelligence.

P3.3 owns the calibrated statistical projection. P3.4 samples that same
projection distribution to verify line/tail behavior, quantify uncertainty,
and produce a single decision contract for product surfaces. The simulation is
therefore a confirmation layer, not an independent model vote.
"""
from __future__ import annotations

import hashlib
import math
import random
from statistics import mean, pstdev
from typing import Any


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def stable_seed(*parts: object) -> int:
    """Return a deterministic 32-bit seed for the supplied decision identity."""
    payload = "|".join(str(part) for part in parts).encode("utf-8")
    return int.from_bytes(hashlib.sha256(payload).digest()[:4], "big")


def _percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * _clamp(q)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _poisson_draw(rng: random.Random, lam: float) -> float:
    """Dependency-light Poisson sampler; prop TD lambdas are intentionally small."""
    if lam <= 0:
        return 0.0
    limit = math.exp(-min(lam, 60.0))
    product = 1.0
    count = 0
    while product > limit and count < 250:
        count += 1
        product *= rng.random()
    return float(max(count - 1, 0))


def _draw_projection(rng: random.Random, intelligence: dict[str, Any]) -> float:
    dist = str(intelligence.get("dist") or "normal")
    if dist == "normal":
        value = rng.gauss(float(intelligence.get("mean") or 0.0), max(float(intelligence.get("sd") or 1.0), 0.01))
        return max(value, 0.0)
    if dist == "lognormal":
        mu = float(intelligence.get("mu") or 0.0)
        sigma = max(float(intelligence.get("sigma") or 0.01), 0.01)
        return max(rng.lognormvariate(mu, sigma) - 8.0, 0.0)
    return _poisson_draw(rng, max(float(intelligence.get("mean") or 0.0), 0.0))


def simulate_prop(
    intelligence: dict[str, Any],
    line: float,
    *,
    side: str = "over",
    simulations: int = 4000,
    seed: int = 40,
) -> dict[str, Any]:
    """Sample a P3.3 projection distribution and summarize line outcomes."""
    count = max(500, min(int(simulations), 20_000))
    chosen_side = "under" if str(side).lower() == "under" else "over"
    rng = random.Random(int(seed))
    values = [_draw_projection(rng, intelligence) for _ in range(count)]
    over_probability = sum(value > float(line) for value in values) / count
    side_probability = over_probability if chosen_side == "over" else 1.0 - over_probability
    calibrated_over = intelligence.get("probOver")
    calibrated_side = None
    if isinstance(calibrated_over, (int, float)):
        calibrated_side = float(calibrated_over) if chosen_side == "over" else 1.0 - float(calibrated_over)
    agreement = None
    if calibrated_side is not None:
        # A 10-point probability gap maps to zero agreement. This is intentionally
        # stricter than raw equality because the simulation samples the same model.
        agreement = _clamp(1.0 - abs(side_probability - calibrated_side) / 0.10)
    return {
        "simulations": count,
        "seed": int(seed),
        "side": chosen_side,
        "line": round(float(line), 3),
        "probOver": round(over_probability, 4),
        "probSide": round(side_probability, 4),
        "agreement": round(agreement, 4) if agreement is not None else None,
        "mean": round(mean(values), 3),
        "sd": round(pstdev(values), 3),
        "p10": round(_percentile(values, 0.10), 2),
        "p50": round(_percentile(values, 0.50), 2),
        "p90": round(_percentile(values, 0.90), 2),
    }


def _decision_grade(
    consensus_probability: float,
    confidence: float,
    agreement: float,
    score: float,
    risk_flags: list[str],
) -> str:
    severe = {"thin_sample", "high_volatility", "roster_unverified"}
    severe_count = len(severe.intersection(risk_flags))
    if (
        consensus_probability >= 0.60
        and confidence >= 0.72
        and agreement >= 0.70
        and score >= 0.62
        and severe_count == 0
    ):
        return "Strong Play"
    if (
        consensus_probability >= 0.56
        and confidence >= 0.62
        and agreement >= 0.62
        and score >= 0.54
        and severe_count <= 1
    ):
        return "Play"
    if consensus_probability >= 0.525 and confidence >= 0.50 and score >= 0.42:
        return "Lean"
    return "Pass"


def _price_status(price: float | int | None, edge: float | None, ev: float | None) -> str:
    if price is None:
        return "unpriced"
    if ev is not None and edge is not None and ev >= 0.02 and edge >= 0.015:
        return "positive_value"
    if ev is not None and ev > 0:
        return "thin_value"
    return "no_value"


def build_prop_decision(
    intelligence: dict[str, Any],
    *,
    side: str,
    line: float,
    price: float | int | None = None,
    edge: float | None = None,
    ev: float | None = None,
    simulations: int = 4000,
    seed: int = 40,
) -> dict[str, Any]:
    """Return the canonical P3.4 decision contract for one player-market side."""
    chosen_side = "under" if str(side).lower() == "under" else "over"
    calibrated_over = float(intelligence.get("probOver") or 0.5)
    model_probability = calibrated_over if chosen_side == "over" else 1.0 - calibrated_over
    simulation = simulate_prop(
        intelligence,
        line,
        side=chosen_side,
        simulations=simulations,
        seed=seed,
    )
    simulation_probability = float(simulation["probSide"])
    # The simulation samples the same underlying distribution; it is a
    # confirmation layer, so it receives a minority weight rather than a model vote.
    consensus_probability = 0.75 * model_probability + 0.25 * simulation_probability
    confidence = float((intelligence.get("confidence") or {}).get("score") or 0.0)
    matchup_quality = float((intelligence.get("matchup") or {}).get("dataQuality") or 0.0)
    agreement = float(simulation.get("agreement") or 0.0)
    signal = abs(consensus_probability - 0.5) * 2.0
    risk_flags = [str(flag) for flag in intelligence.get("riskFlags") or []]
    risk_penalty = min(0.18, 0.045 * len(risk_flags))
    model_score = _clamp(
        0.34 * confidence
        + 0.30 * signal
        + 0.20 * agreement
        + 0.10 * matchup_quality
        + 0.06 * float(bool(intelligence.get("n")))
        - risk_penalty
    )
    grade = _decision_grade(consensus_probability, confidence, agreement, model_score, risk_flags)
    price_status = _price_status(price, edge, ev)
    actionable = grade in {"Strong Play", "Play"} and price_status == "positive_value"

    reasons = [
        f"{chosen_side.title()} consensus probability {consensus_probability:.1%}",
        f"Evidence confidence {confidence:.0%}",
        f"Simulation agreement {agreement:.0%}",
    ]
    matchup = intelligence.get("matchup") or {}
    if matchup.get("grade") and matchup.get("grade") != "neutral":
        reasons.append(f"{str(matchup['grade']).title()} matchup context")
    risks = list(risk_flags)
    if agreement < 0.70:
        risks.append("simulation_model_gap")
    if price is None:
        risks.append("unpriced_market")
    elif price_status == "no_value":
        risks.append("price_not_supportive")

    if actionable:
        action = f"Consider {chosen_side.upper()} at the verified price; positive model/price value is present."
    elif grade in {"Strong Play", "Play"} and price is None:
        action = f"Model pick: {chosen_side.upper()}; wait for a verified sportsbook price before treating it as actionable."
    elif grade == "Lean":
        action = f"Lean {chosen_side.upper()}; monitor the line, price, and late information."
    else:
        action = "Pass at the current evidence and/or price."

    return {
        "decisionGrade": grade,
        "decisionScore": round(model_score, 4),
        "side": chosen_side,
        "modelProbability": round(model_probability, 4),
        "simulationProbability": round(simulation_probability, 4),
        "consensusProbability": round(consensus_probability, 4),
        "simulationAgreement": round(agreement, 4),
        "priceStatus": price_status,
        "actionable": actionable,
        "recommendedAction": action,
        "decisionReasons": reasons[:4],
        "decisionRisks": risks[:5],
        "simulation": simulation,
        "modelVersion": "p3.4-simulation-decision",
    }


def summarize_decisions(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate structural coverage for the read-only P3.4 production gate."""
    grades = {"Strong Play": 0, "Play": 0, "Lean": 0, "Pass": 0}
    simulation_rows = agreement_rows = bounded_rows = 0
    for row in rows:
        grade = str(row.get("decisionGrade") or "Pass")
        grades[grade] = grades.get(grade, 0) + 1
        if row.get("simulationProbability") is not None:
            simulation_rows += 1
        agreement = row.get("simulationAgreement")
        if isinstance(agreement, (int, float)):
            agreement_rows += 1
        probability = row.get("consensusProbability")
        if isinstance(probability, (int, float)) and 0.0 <= probability <= 1.0:
            bounded_rows += 1
    total = len(rows)
    return {
        "rows": total,
        "grades": grades,
        "leanOrBetter": grades.get("Strong Play", 0) + grades.get("Play", 0) + grades.get("Lean", 0),
        "playOrBetter": grades.get("Strong Play", 0) + grades.get("Play", 0),
        "simulationCoverage": round(simulation_rows / total, 4) if total else 0.0,
        "agreementCoverage": round(agreement_rows / total, 4) if total else 0.0,
        "probabilityCoverage": round(bounded_rows / total, 4) if total else 0.0,
    }
