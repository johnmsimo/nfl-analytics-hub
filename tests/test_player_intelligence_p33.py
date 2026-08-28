"""P3.3 evidence-aware player intelligence regression coverage."""
from __future__ import annotations

from pathlib import Path

import player_intelligence as pi

ROOT = Path(__file__).resolve().parents[1]


def _qb_rows(count: int = 12) -> list[dict]:
    yards = [228, 251, 274, 239, 286, 263, 247, 301, 278, 292, 267, 284]
    touchdowns = [1, 2, 2, 1, 3, 2, 1, 3, 2, 2, 1, 2]
    return [
        {
            "passing_yards": yards[index],
            "passing_tds": touchdowns[index],
            "interceptions": 0,
            "sacks": 2,
            "carries": 4,
            "rushing_yards": 18,
            "rushing_tds": 0,
            "receptions": 0,
            "targets": 0,
            "receiving_yards": 0,
            "receiving_tds": 0,
            "fumbles_lost": 0,
            "position": "QB",
        }
        for index in range(count)
    ]


def _dvp(ratio: float = 1.12, games: int = 12) -> dict:
    return {
        "OPP": {
            "games": games,
            "QB": {
                "passing_yards_ratio": ratio,
                "passing_tds_ratio": ratio,
                "rushing_yards_ratio": 1.0,
                "rushing_tds_ratio": 1.0,
                "receiving_tds_ratio": 1.0,
            },
        }
    }


def test_intelligence_adds_ordered_uncertainty_and_calibrated_probability():
    result = pi.analyze_projection(
        _qb_rows(),
        "pass_yds",
        opponent="OPP",
        dvp=_dvp(),
        position="QB",
        line=249.5,
        roster_verified=True,
    )
    assert result is not None
    assert result["interval"]["p10"] < result["interval"]["p50"] < result["interval"]["p90"]
    assert 0.0 <= result["probOver"] <= 1.0
    assert 0.0 <= result["rawProbOver"] <= 1.0
    assert abs(result["probOver"] - 0.5) <= abs(result["rawProbOver"] - 0.5)
    assert result["confidence"]["score"] > 0.70
    assert result["modelVersion"] == "p3.3-evidence-calibrated"


def test_thin_evidence_has_lower_confidence_than_deep_history():
    thin = pi.analyze_projection(_qb_rows(4), "pass_yds", position="QB", line=249.5)
    deep = pi.analyze_projection(_qb_rows(12), "pass_yds", position="QB", line=249.5)
    assert thin is not None and deep is not None
    assert thin["confidence"]["score"] < deep["confidence"]["score"]
    assert abs(thin["probOver"] - 0.5) <= abs(thin["rawProbOver"] - 0.5)


def test_matchup_grade_tracks_damped_defense_factor():
    favorable = pi.analyze_projection(
        _qb_rows(), "pass_yds", opponent="OPP", dvp=_dvp(1.30), position="QB", line=250.5
    )
    tough = pi.analyze_projection(
        _qb_rows(), "pass_yds", opponent="OPP", dvp=_dvp(0.70), position="QB", line=250.5
    )
    assert favorable is not None and tough is not None
    assert favorable["matchup"]["grade"] == "favorable"
    assert tough["matchup"]["grade"] == "tough"
    assert favorable["matchup"]["factor"] > 1.0
    assert tough["matchup"]["factor"] < 1.0


def test_ranking_score_uses_model_confidence_and_available_value():
    intelligence = pi.analyze_projection(
        _qb_rows(), "pass_yds", opponent="OPP", dvp=_dvp(), position="QB", line=249.5
    )
    assert intelligence is not None
    model_only = pi.ranking_score(intelligence)
    priced = pi.ranking_score(intelligence, edge=0.08, ev=0.06)
    assert 0.0 <= model_only <= 1.0
    assert model_only < priced <= 1.0


def test_leave_forward_backtest_is_bounded_and_has_samples():
    rows = _qb_rows()
    logs = {"qb-1": rows, "qb-2": list(rows)}
    result = pi.backtest_market(logs, "pass_yds", min_prior_games=4)
    assert result["n"] > 0
    assert 0.0 <= result["brier"] <= 1.0
    assert 0.0 <= result["ece"] <= 1.0
    assert result["reliability"]


def test_props_and_analytics_use_p33_intelligence_layer():
    props = (ROOT / "routes" / "props.py").read_text(encoding="utf-8")
    analytics = (ROOT / "routes" / "intelligence.py").read_text(encoding="utf-8")
    board = (ROOT / "props.html").read_text(encoding="utf-8")
    for source in (props, analytics):
        assert "import player_intelligence as pi" in source
        assert "pi.analyze_projection(" in source
    assert 'sortKey = \'rankScore\'' in board
    assert "confidenceScore" in board
    assert "matchupGrade" in board


def test_p33_workflow_is_read_only_and_avoids_paid_provider_calls():
    workflow = (
        ROOT / ".github" / "workflows" / "p33-player-intelligence-verification.yml"
    ).read_text(encoding="utf-8")
    assert "RUN_PLAYER_INTELLIGENCE_VERIFY" in workflow
    assert "environment: production" in workflow
    assert "/app/scripts/p33_player_intelligence_verification.py" in workflow
    assert "/app/scripts/p2_exit_verification.py" in workflow
    assert "ODDS_API" not in workflow
    assert "sync_commercial" not in workflow
    assert "retention" not in workflow.lower()
