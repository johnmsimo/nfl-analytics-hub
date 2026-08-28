"""Analytics and rankings APIs for the Option D product sections."""
from __future__ import annotations

from collections import defaultdict

from flask import Blueprint, jsonify, request

import decision_intelligence as di
import nfl_data
import player_intelligence as pi
import projection_data as pd
import projections as pj
from routes.dashboard_api import _team_power

intelligence_bp = Blueprint("intelligence", __name__)


def _season() -> tuple[int, int]:
    raw = request.args.get("season")
    if raw is not None:
        season = int(raw)
    else:
        try:
            season = int(nfl_data.current_week()["season"])
        except Exception:
            season = int(nfl_data.default_season())
    try:
        stats = pd.stats_season(season)
    except Exception:
        stats = season - 1
    return season, stats


def _schedule(season: int) -> list[dict]:
    try:
        return nfl_data.get_schedule(season)
    except Exception:
        return []


def _next_opponent(team: str, season: int) -> str | None:
    for game in _schedule(season):
        if not game.get("completed") and team in (game.get("home_team"), game.get("away_team")):
            return game["away_team"] if game["home_team"] == team else game["home_team"]
    return None


@intelligence_bp.route("/api/analytics")
def api_analytics():
    season, stats_season = _season()
    summaries = nfl_data.team_summaries(stats_season)
    logs = pd.player_game_logs(stats_season)
    index = pd.player_index(season, stats_season)
    dvp = pd.defense_vs_position(stats_season)

    teams = []
    for code, row in summaries.items():
        diff = round(row.get("ppg", 0) - row.get("papg", 0), 1)
        teams.append(
            {
                "team": code,
                "record": row.get("record", "0-0"),
                "games": row.get("games", 0),
                "ppg": row.get("ppg", 0),
                "papg": row.get("papg", 0),
                "point_diff": diff,
                "power_score": _team_power(row),
                "next_opponent": _next_opponent(code, season),
            }
        )
    teams.sort(key=lambda row: (row["power_score"] is None, -(row["power_score"] or 0)))

    market_rows = []
    position_counts = defaultdict(int)
    for player_id, meta in index.items():
        history = logs.get(player_id, [])
        if len(history) < 3:
            continue
        position = meta["position"]
        position_counts[position] += 1
        opponent = _next_opponent(meta["team"], season)
        if not opponent:
            continue
        for market in pj.relevant_markets(position):
            preview = pi.analyze_projection(
                history,
                market,
                opponent=opponent,
                dvp=dvp,
                position=position,
                roster_verified=bool(meta.get("rosterVerified")),
            )
            if not preview or preview["mean"] < pj.MIN_MEAN[market]:
                continue
            line = 0.5 if market == "anytime_td" else int(preview["mean"]) + 0.5
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
            prob_over = float(intelligence["probOver"])
            side = "over" if prob_over >= 0.5 else "under"
            decision = di.build_prop_decision(
                intelligence,
                side=side,
                line=float(line),
                simulations=800,
                seed=di.stable_seed(season, player_id, market, line, "analytics"),
            )
            market_rows.append(
                {
                    "playerId": player_id,
                    "player": meta["name"],
                    "team": meta["team"],
                    "position": position,
                    "marketKey": market,
                    "marketLabel": pj.MARKET_LABELS[market],
                    "projection": round(intelligence["mean"], 1),
                    "projectionRange": intelligence["interval"],
                    "line": line,
                    "rawProbOver": intelligence["rawProbOver"],
                    "probOver": intelligence["probOver"],
                    "side": side,
                    "opponent": opponent,
                    "matchupGrade": intelligence["matchup"]["grade"],
                    "matchupFactor": intelligence["matchup"]["factor"],
                    "confidenceScore": intelligence["confidence"]["score"],
                    "confidenceGrade": intelligence["confidence"]["grade"],
                    "riskFlags": intelligence["riskFlags"],
                    "signal": intelligence["signalStrength"],
                    "modelRankScore": pi.ranking_score(intelligence),
                    "rankScore": decision["decisionScore"],
                    "decisionGrade": decision["decisionGrade"],
                    "decisionScore": decision["decisionScore"],
                    "consensusProb": decision["consensusProbability"],
                    "simulationProb": decision["simulationProbability"],
                    "simulationAgreement": decision["simulationAgreement"],
                    "recommendedAction": decision["recommendedAction"],
                    "decisionReasons": decision["decisionReasons"],
                    "decisionRisks": decision["decisionRisks"],
                    "evidenceSeason": stats_season,
                    "rosterVerified": bool(meta.get("rosterVerified")),
                    "modelVersion": decision["modelVersion"],
                }
            )
    market_rows.sort(key=lambda row: row["decisionScore"], reverse=True)

    total_games = sum(team["games"] for team in teams)
    avg_points = round(sum(team["ppg"] for team in teams) / max(len(teams), 1), 1)
    avg_diff = round(sum(abs(team["point_diff"]) for team in teams) / max(len(teams), 1), 1)
    return jsonify(
        {
            "season": season,
            "stats_season": stats_season,
            "model_version": "p3.4-simulation-decision",
            "kpis": {
                "teams": len(teams),
                "player_pool": len(index),
                "team_games": total_games,
                "avg_points": avg_points,
                "avg_abs_diff": avg_diff,
                "projection_signals": len(market_rows),
                "lean_or_better": sum(
                    1 for row in market_rows if row["decisionGrade"] in {"Strong Play", "Play", "Lean"}
                ),
                "play_or_better": sum(
                    1 for row in market_rows if row["decisionGrade"] in {"Strong Play", "Play"}
                ),
            },
            "team_efficiency": teams,
            "top_signals": market_rows[:20],
            "position_pool": [
                {"position": position, "players": players}
                for position, players in sorted(position_counts.items())
            ],
            "methodology": (
                "P3.4 keeps P3.3's normalized warehouse projections and evidence-aware "
                "calibration, then samples each projection distribution with deterministic "
                "Monte Carlo to confirm tail behavior and assign one model decision grade. "
                "Price/EV remains a separate actionability layer; simulation is a confirmation "
                "of the same model distribution, not an independent model vote."
            ),
        }
    )


@intelligence_bp.route("/api/rankings")
def api_rankings():
    season, stats_season = _season()
    summaries = nfl_data.team_summaries(stats_season)
    logs = pd.player_game_logs(stats_season)
    index = pd.player_index(season, stats_season)

    team_rows = [
        {
            **row,
            "power_score": _team_power(row),
            "point_diff": round(row.get("ppg", 0) - row.get("papg", 0), 1),
        }
        for row in summaries.values()
    ]
    team_rows.sort(key=lambda row: (row["power_score"] is None, -(row["power_score"] or 0)))
    for rank, row in enumerate(team_rows, 1):
        row["rank"] = rank

    leaders: dict[str, list[dict]] = {"QB": [], "RB": [], "WR": [], "TE": []}
    for player_id, meta in index.items():
        position = meta.get("position")
        if position not in leaders:
            continue
        history = logs.get(player_id, [])
        if len(history) < 3:
            continue
        games = len(history)
        if position == "QB":
            primary = sum(row["passing_yards"] for row in history) / games
            secondary = sum(row["passing_tds"] for row in history) / games
            score = primary / 8 + secondary * 8
            metric = "Pass Yds/G"
        elif position == "RB":
            primary = sum(row["rushing_yards"] + row["receiving_yards"] for row in history) / games
            secondary = sum(row["carries"] + row["targets"] for row in history) / games
            score = primary / 3 + secondary
            metric = "Scrim Yds/G"
        else:
            primary = sum(row["receiving_yards"] for row in history) / games
            secondary = sum(row["targets"] for row in history) / games
            score = primary / 2.5 + secondary * 1.5
            metric = "Rec Yds/G"
        leaders[position].append(
            {
                "playerId": player_id,
                "player": meta["name"],
                "team": meta["team"],
                "games": games,
                "primary": round(primary, 1),
                "usage": round(secondary, 1),
                "score": round(score, 1),
                "metric": metric,
                "evidenceSeason": stats_season,
                "rosterVerified": bool(meta.get("rosterVerified")),
            }
        )
    for position, rows in leaders.items():
        rows.sort(key=lambda row: row["score"], reverse=True)
        leaders[position] = rows[:12]
        for rank, row in enumerate(leaders[position], 1):
            row["rank"] = rank

    return jsonify(
        {"season": season, "stats_season": stats_season, "teams": team_rows, "leaders": leaders}
    )
