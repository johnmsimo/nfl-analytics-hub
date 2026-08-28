"""Read-only production verification helpers for P3.4."""
from __future__ import annotations

from typing import Any

import decision_intelligence as di
import nfl_data
import player_intelligence as pi
import projection_data as pd
import projections as pj

_SKILL = frozenset({"QB", "RB", "FB", "WR", "TE"})


def _next_opponents(schedule: list[dict[str, Any]]) -> dict[str, str]:
    opponents: dict[str, str] = {}
    for game in schedule:
        if game.get("completed"):
            continue
        home = game.get("home_team")
        away = game.get("away_team")
        if home and away:
            opponents.setdefault(str(home), str(away))
            opponents.setdefault(str(away), str(home))
    return opponents


def _reference_line(preview: dict[str, Any], market: str) -> float:
    if market == "anytime_td":
        return 0.5
    return float(int(float(preview["mean"])) + 0.5)


def readiness_snapshot(target_season: int = 2026, simulations: int = 600) -> dict[str, Any]:
    """Verify P3.4 decision coverage without sportsbook or commercial-provider calls."""
    evidence_season = pd.stats_season(target_season)
    logs = pd.player_game_logs(evidence_season)
    index = pd.player_index(target_season, evidence_season)
    dvp = pd.defense_vs_position(evidence_season)
    opponents = _next_opponents(nfl_data.get_schedule(target_season))

    rows: list[dict[str, Any]] = []
    eligible_players = 0
    for player_id, meta in index.items():
        position = str(meta.get("position") or "").upper()
        if position not in _SKILL:
            continue
        history = logs.get(player_id, [])
        opponent = opponents.get(str(meta.get("team") or ""))
        if len(history) < 3 or not opponent:
            continue
        eligible_players += 1
        for market in pj.relevant_markets(position):
            preview = pi.analyze_projection(
                history,
                market,
                opponent=opponent,
                dvp=dvp,
                position=position,
                roster_verified=bool(meta.get("rosterVerified")),
            )
            if not preview or float(preview["mean"]) < float(pj.MIN_MEAN[market]):
                continue
            line = _reference_line(preview, market)
            intelligence = pi.analyze_projection(
                history,
                market,
                opponent=opponent,
                dvp=dvp,
                position=position,
                line=line,
                roster_verified=bool(meta.get("rosterVerified")),
            )
            if not intelligence or intelligence.get("probOver") is None:
                continue
            p_over = float(intelligence["probOver"])
            side = "over" if p_over >= 0.5 else "under"
            decision = di.build_prop_decision(
                intelligence,
                side=side,
                line=line,
                simulations=simulations,
                seed=di.stable_seed(target_season, player_id, market, line, "verify"),
            )
            rows.append(decision)

    summary = di.summarize_decisions(rows)
    agreements = [
        float(row["simulationAgreement"])
        for row in rows
        if isinstance(row.get("simulationAgreement"), (int, float))
    ]
    average_agreement = sum(agreements) / len(agreements) if agreements else 0.0
    thresholds = {
        "minimumDecisionRows": 500,
        "minimumEligiblePlayers": 250,
        "minimumLeanOrBetter": 75,
        "minimumPlayOrBetter": 10,
        "minimumSimulationCoverage": 0.99,
        "minimumAgreementCoverage": 0.99,
        "minimumProbabilityCoverage": 1.0,
        "minimumAverageAgreement": 0.70,
    }
    gates = {
        "decision_volume": summary["rows"] >= thresholds["minimumDecisionRows"],
        "eligible_player_pool": eligible_players >= thresholds["minimumEligiblePlayers"],
        "lean_or_better_pool": summary["leanOrBetter"] >= thresholds["minimumLeanOrBetter"],
        "play_or_better_pool": summary["playOrBetter"] >= thresholds["minimumPlayOrBetter"],
        "simulation_coverage": summary["simulationCoverage"] >= thresholds["minimumSimulationCoverage"],
        "agreement_coverage": summary["agreementCoverage"] >= thresholds["minimumAgreementCoverage"],
        "probability_bounds": summary["probabilityCoverage"] >= thresholds["minimumProbabilityCoverage"],
        "simulation_agreement": average_agreement >= thresholds["minimumAverageAgreement"],
    }
    return {
        "phase": "P3.4",
        "mode": "read-only",
        "targetSeason": target_season,
        "evidenceSeason": evidence_season,
        "eligiblePlayers": eligible_players,
        "decisionSummary": summary,
        "averageSimulationAgreement": round(average_agreement, 4),
        "thresholds": thresholds,
        "gates": gates,
        "ok": all(gates.values()),
    }
