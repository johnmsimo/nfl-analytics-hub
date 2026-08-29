from __future__ import annotations

import copy

import p40_game_intelligence as p40
import p43_game_decision_delivery as p43
import p50_game_calibration_promotion as p50


def _eligible_report() -> dict:
    return {
        "available": True,
        "model": "p4.9-game-calibration-challenger",
        "modelVersion": "p49-challenger-v1",
        "state": "review",
        "promotionGate": {
            "eligible": True,
            "requiresHumanReview": True,
            "automaticApply": False,
            "checks": {"forwardHoldoutIntegrity": True},
        },
        "candidate": {
            "candidateId": "p49-test-candidate",
            "family": "logit-affine",
            "parameters": {"slope": 0.85, "intercept": 0.02},
            "validationIsForwardHoldout": True,
            "validation": {
                "perMarketChampion": {
                    "moneyline": {"samples": 12, "brier": 0.240, "ece": 0.080},
                },
                "perMarketChallenger": {
                    "moneyline": {"samples": 12, "brier": 0.220, "ece": 0.075},
                },
            },
        },
    }


def test_p50_accepts_p49_review_only_with_moneyline_holdout_guard():
    review = p50.assess_candidate_report(_eligible_report())
    assert review["eligible"] is True
    assert not review["failedChecks"]
    assert review["moneyline"]["brierDelta"] < 0


def test_p50_rejects_candidate_when_moneyline_regresses():
    report = _eligible_report()
    report["candidate"]["validation"]["perMarketChallenger"]["moneyline"]["brier"] = 0.250
    review = p50.assess_candidate_report(report)
    assert review["eligible"] is False
    assert "moneylineBrierNotRegressed" in review["failedChecks"]


def test_p50_rejects_candidate_without_enough_moneyline_holdout_samples():
    report = _eligible_report()
    report["candidate"]["validation"]["perMarketChallenger"]["moneyline"]["samples"] = 4
    review = p50.assess_candidate_report(report)
    assert review["eligible"] is False
    assert "moneylineValidationSampleFloor" in review["failedChecks"]


def test_p50_promotion_requires_exact_confirmation():
    result = p50.promote_candidate(
        "p49-test-candidate",
        confirmation="YES",
        actor="owner",
        persist=False,
        report=_eligible_report(),
    )
    assert result["ok"] is False
    assert result["code"] == "CONFIRMATION_REQUIRED"


def test_p50_dry_run_promotion_is_explicit_and_zero_write():
    result = p50.promote_candidate(
        "p49-test-candidate",
        confirmation=p50.PROMOTE_CONFIRMATION,
        actor="owner",
        persist=False,
        report=_eligible_report(),
    )
    assert result["ok"] is True
    assert result["dryRun"] is True
    assert result["wouldApply"] is True
    assert result["event"]["action"] == "promote"
    assert result["event"]["candidateId"] == "p49-test-candidate"


def test_p50_dry_run_rejects_candidate_id_mismatch():
    result = p50.promote_candidate(
        "wrong-candidate",
        confirmation=p50.PROMOTE_CONFIRMATION,
        actor="owner",
        persist=False,
        report=_eligible_report(),
    )
    assert result["ok"] is False
    assert result["code"] == "CANDIDATE_MISMATCH"


def test_p50_calibration_never_flips_selected_side():
    champion = {
        "state": "promoted",
        "applied": True,
        "candidateId": "p49-test-candidate",
        "family": "logit-affine",
        "parameters": {"slope": 0.5, "intercept": -0.3},
        "approvedAt": "2026-08-29T00:00:00+00:00",
    }
    result = p50.apply_to_selected_probability(0.51, champion=champion)
    assert result["applied"] is True
    assert 0.5 <= result["probability"] <= 0.999
    assert result["candidateId"] == "p49-test-candidate"


def test_p40_applies_promoted_probability_and_preserves_provenance(monkeypatch):
    monkeypatch.setattr(
        p40,
        "_promotion_calibration",
        lambda probability: {
            "probability": 0.71,
            "rawProbability": probability,
            "applied": True,
            "candidateId": "p49-test-candidate",
            "modelVersion": p50.MODEL_VERSION,
            "championState": "promoted",
        },
    )
    game = {
        "game_id": "game-1",
        "season": 2026,
        "season_type": "REG",
        "week": 1,
        "date": "2026-09-10T00:15:00Z",
        "home_team": "SEA",
        "away_team": "NE",
    }
    home = {
        "rating": 5.0,
        "evidenceQuality": 0.9,
        "evidenceMode": "current-season",
        "advanced": {"available": True},
    }
    away = {
        "rating": 0.0,
        "evidenceQuality": 0.9,
        "evidenceMode": "current-season",
        "advanced": {"available": True},
    }
    decision = p40.predict_game(game, home, away)
    assert decision["calibration"]["applied"] is True
    assert decision["selectedProbability"] == 0.71
    assert decision["modelVersion"].endswith("+p49-test-candidate")
    assert decision["baseModelVersion"] == p40.MODEL_VERSION
    assert decision["prePromotionSelectedProbability"] != decision["selectedProbability"]


def test_p43_preserves_effective_model_and_calibration_provenance():
    board = {
        "available": True,
        "season": 2026,
        "seasonType": "REG",
        "week": 1,
        "rows": [
            {
                "gameId": "game-1",
                "season": 2026,
                "seasonType": "REG",
                "week": 1,
                "homeTeam": "SEA",
                "awayTeam": "NE",
                "modelVersion": "p40-transparent-v1+p49-test-candidate",
                "selectedSide": "home",
                "selectedTeam": "SEA",
                "selectedProbability": 0.71,
                "confidenceScore": 72.0,
                "decisionGrade": "Play",
                "calibration": {
                    "applied": True,
                    "candidateId": "p49-test-candidate",
                },
                "markets": {},
            }
        ],
    }
    item = p43.flatten_board(copy.deepcopy(board))[0]
    assert item["sourceModelVersion"] == "p40-transparent-v1+p49-test-candidate"
    assert item["calibration"]["candidateId"] == "p49-test-candidate"
    assert item["modelProbability"] == 0.71
