from __future__ import annotations

import decision_intelligence as di


def _intel(prob_over: float = 0.70, confidence: float = 0.85, risks: list[str] | None = None):
    return {
        "dist": "normal",
        "mean": 105.7,
        "sd": 10.0,
        "n": 12,
        "probOver": prob_over,
        "confidence": {"score": confidence},
        "matchup": {"dataQuality": 0.8, "grade": "favorable"},
        "riskFlags": list(risks or []),
    }


def test_simulation_is_deterministic_and_bounded():
    intel = _intel()
    first = di.simulate_prop(intel, 100.5, simulations=2000, seed=123)
    second = di.simulate_prop(intel, 100.5, simulations=2000, seed=123)

    assert first == second
    assert first["simulations"] == 2000
    assert 0.0 <= first["probOver"] <= 1.0
    assert 0.0 <= first["probSide"] <= 1.0
    assert 0.0 <= first["agreement"] <= 1.0
    assert first["p10"] <= first["p50"] <= first["p90"]


def test_strong_model_pick_stays_unpriced_not_actionable():
    decision = di.build_prop_decision(
        _intel(),
        side="over",
        line=100.5,
        simulations=3000,
        seed=7,
    )

    assert decision["decisionGrade"] in {"Strong Play", "Play"}
    assert decision["priceStatus"] == "unpriced"
    assert decision["actionable"] is False
    assert "wait for a verified sportsbook price" in decision["recommendedAction"]
    assert "unpriced_market" in decision["decisionRisks"]


def test_positive_price_can_make_play_actionable_without_changing_model_grade():
    decision = di.build_prop_decision(
        _intel(),
        side="over",
        line=100.5,
        price=110,
        edge=0.08,
        ev=0.12,
        simulations=3000,
        seed=7,
    )

    assert decision["decisionGrade"] in {"Strong Play", "Play"}
    assert decision["priceStatus"] == "positive_value"
    assert decision["actionable"] is True
    assert decision["consensusProbability"] >= 0.60


def test_low_evidence_or_severe_risk_fails_closed():
    intel = _intel(prob_over=0.54, confidence=0.40, risks=["thin_sample", "high_volatility"])
    intel["mean"] = 101.5
    decision = di.build_prop_decision(
        intel,
        side="over",
        line=100.5,
        simulations=1500,
        seed=9,
    )

    assert decision["decisionGrade"] == "Pass"
    assert decision["actionable"] is False
    assert decision["recommendedAction"].startswith("Pass")


def test_summary_reports_pick_pool_and_structural_coverage():
    rows = [
        {
            "decisionGrade": "Strong Play",
            "simulationProbability": 0.64,
            "simulationAgreement": 0.91,
            "consensusProbability": 0.63,
        },
        {
            "decisionGrade": "Lean",
            "simulationProbability": 0.55,
            "simulationAgreement": 0.88,
            "consensusProbability": 0.56,
        },
        {
            "decisionGrade": "Pass",
            "simulationProbability": 0.51,
            "simulationAgreement": 0.82,
            "consensusProbability": 0.51,
        },
    ]
    summary = di.summarize_decisions(rows)

    assert summary["rows"] == 3
    assert summary["grades"]["Strong Play"] == 1
    assert summary["grades"]["Lean"] == 1
    assert summary["leanOrBetter"] == 2
    assert summary["playOrBetter"] == 1
    assert summary["simulationCoverage"] == 1.0
    assert summary["agreementCoverage"] == 1.0
    assert summary["probabilityCoverage"] == 1.0


def test_stable_seed_is_repeatable_and_identity_sensitive():
    assert di.stable_seed("game", "player", "rush_yds", 70.5) == di.stable_seed(
        "game", "player", "rush_yds", 70.5
    )
    assert di.stable_seed("game", "player", "rush_yds", 70.5) != di.stable_seed(
        "game", "player2", "rush_yds", 70.5
    )
