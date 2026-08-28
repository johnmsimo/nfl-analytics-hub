"""Aggregated payload for the AI Intelligence dashboard and P3.5 My Hub delivery."""

from __future__ import annotations

import math
import statistics
from datetime import UTC, datetime

from flask import Blueprint, jsonify, request

import decision_delivery as dd
import nfl_data
import odds_api
from routes.games import game_lines
from routes.props import _build_game_rows

dashboard_bp = Blueprint("dashboard", __name__)


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _is_number(value) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(float(value))


def _team_stats_available(team: dict | None) -> bool:
    """Require real played-game inputs before calculating team analytics."""
    if not isinstance(team, dict):
        return False
    return (
        _is_number(team.get("games"))
        and team["games"] > 0
        and _is_number(team.get("wins"))
        and _is_number(team.get("ppg"))
        and _is_number(team.get("papg"))
    )


def _team_power(team: dict) -> float | None:
    """Return a power score only when its source performance data exists."""
    if not _team_stats_available(team):
        return None
    games = team["games"]
    win_pct = team["wins"] / games
    diff = team["ppg"] - team["papg"]
    return round(_clamp(50 + win_pct * 35 + diff * 1.8, 0, 100), 1)


def _point_diff(team: dict) -> float | None:
    if not _team_stats_available(team):
        return None
    return round(team["ppg"] - team["papg"], 1)


def _game_prediction(game: dict, teams: dict, lines: dict) -> dict:
    home = teams.get(game["home_team"], {})
    away = teams.get(game["away_team"], {})
    hp = _team_power(home)
    ap = _team_power(away)
    if hp is None or ap is None:
        missing = []
        if hp is None:
            missing.append(game["home_team"])
        if ap is None:
            missing.append(game["away_team"])
        return {
            "game_id": game["game_id"],
            "date": game.get("date"),
            "home_team": game["home_team"],
            "away_team": game["away_team"],
            "status": "unavailable",
            "reason": "Team performance data unavailable for " + ", ".join(missing),
            "home_prob": None,
            "away_prob": None,
            "confidence": None,
            "projected_home": None,
            "projected_away": None,
            "factors": [],
            "market": lines,
        }
    logit = (hp - ap + 2.2) / 11.5
    home_prob = 1 / (1 + math.exp(-logit))
    confidence = _clamp(0.58 + abs(home_prob - 0.5) * 0.7, 0.58, 0.94)
    base_total = statistics.mean((home["ppg"], away["ppg"], home["papg"], away["papg"]))
    projected_total = _clamp(base_total * 2, 33, 58)
    spread = (home_prob - 0.5) * 20
    home_score = projected_total / 2 + spread / 2
    away_score = projected_total - home_score
    return {
        "game_id": game["game_id"],
        "date": game.get("date"),
        "home_team": game["home_team"],
        "away_team": game["away_team"],
        "status": "ready",
        "reason": None,
        "home_prob": round(home_prob, 4),
        "away_prob": round(1 - home_prob, 4),
        "confidence": round(confidence, 4),
        "projected_home": round(home_score, 1),
        "projected_away": round(away_score, 1),
        "factors": [
            {
                "key": "home_power",
                "label": f"{game['home_team']} power score",
                "value": hp,
                "unit": "rating",
                "source": "team_performance",
            },
            {
                "key": "away_power",
                "label": f"{game['away_team']} power score",
                "value": ap,
                "unit": "rating",
                "source": "team_performance",
            },
            {
                "key": "home_field",
                "label": "Home-field adjustment",
                "value": 2.2,
                "unit": "rating points",
                "source": "model_assumption",
            },
            {
                "key": "market",
                "label": "Market context",
                "value": "available" if lines.get("available") else "unavailable",
                "unit": None,
                "source": "odds_provider",
            },
        ],
        "market": lines,
    }


def _status(available: int, expected: int | None = None) -> str:
    if available <= 0:
        return "unavailable"
    if expected is not None and available < expected:
        return "partial"
    return "ready"


def _reason(code: str, message: str | None) -> dict:
    return {"code": code, "message": message or code.replace("_", " ")}


def _dashboard_payload(season: int, week: int, stype: str) -> dict:
    ss = nfl_data.stats_season(season)
    games = nfl_data.get_week_games(season, week, stype)
    teams = nfl_data.team_summaries(ss)

    rankings = [
        {**row, "power_score": _team_power(row), "point_diff": _point_diff(row)}
        for row in teams.values()
    ]
    rankings.sort(key=lambda row: (row["power_score"] is None, -(row["power_score"] or 0)))
    valid_team_count = sum(row["power_score"] is not None for row in rankings)

    predictions = []
    projection_rows: list[dict] = []
    projection_errors = 0
    market_errors = 0
    odds_configured = odds_api.is_configured()
    evaluated_games = games[:8]
    for game in evaluated_games:
        if odds_configured:
            try:
                lines = game_lines(game)
            except Exception:  # noqa: BLE001 - dashboard degrades without provider details
                market_errors += 1
                lines = {
                    "available": False,
                    "status": "degraded",
                    "reason": "Market provider unavailable",
                }
        else:
            lines = {
                "available": False,
                "status": "unavailable",
                "reason": "Odds provider is not configured",
            }
        predictions.append(_game_prediction(game, teams, lines))
        try:
            projection_rows.extend(_build_game_rows(game, season))
        except Exception:  # noqa: BLE001 - delivery state reports the failed game
            projection_errors += 1

    projection_rows = dd.sort_decisions(projection_rows)
    quick_props = dd.build_delivery(
        projection_rows,
        limit=8,
        game_errors=projection_errors,
        expected_games=len(evaluated_games),
    )
    top_players = quick_props["picks"] or quick_props["watchlist"]
    ready_predictions = [p for p in predictions if p["status"] == "ready"]
    featured = ready_predictions[0] if ready_predictions else None
    priced_edges = [r["edge"] for r in projection_rows if _is_number(r.get("edge"))]
    market_edge = max(priced_edges) if priced_edges else None
    market_available = any(p["market"].get("available") for p in predictions)

    components = {
        "schedule": {
            "status": _status(len(games)),
            "available_count": len(games),
            "message": None if games else "No games are available for the selected week.",
        },
        "team_performance": {
            "status": _status(valid_team_count, 32),
            "available_count": valid_team_count,
            "expected_count": 32,
            "message": None
            if valid_team_count >= 32
            else f"Team performance is available for {valid_team_count} of 32 teams.",
        },
        "game_predictions": {
            "status": _status(len(ready_predictions), len(predictions) or None),
            "available_count": len(ready_predictions),
            "expected_count": len(predictions),
            "message": None
            if len(ready_predictions) == len(predictions) and predictions
            else "One or more matchups lack required team performance inputs.",
        },
        "player_projections": {
            "status": "degraded" if projection_errors else _status(len(projection_rows)),
            "available_count": len(projection_rows),
            "error_count": projection_errors,
            "message": (
                "Projection generation failed for one or more games."
                if projection_errors
                else (None if projection_rows else "No qualifying player projection data is available.")
            ),
        },
        "quick_props": {
            "status": quick_props["state"],
            "available_count": quick_props["summary"]["delivered"],
            "watchlist_count": len(quick_props["watchlist"]),
            "message": quick_props["message"],
        },
        "market_pricing": {
            "status": (
                "degraded"
                if market_errors
                else ("ready" if market_available or priced_edges else "unavailable")
            ),
            "available_count": len(priced_edges),
            "error_count": market_errors,
            "message": (
                "Market provider failed for one or more games."
                if market_errors
                else (
                    None
                    if market_available or priced_edges
                    else (
                        "No market prices are currently available."
                        if odds_configured
                        else "Odds provider is not configured."
                    )
                )
            ),
        },
        "trend_history": {
            "status": "unavailable",
            "available_count": 0,
            "message": "Historical power-score snapshots are not collected yet.",
        },
    }
    reasons = []
    if not games:
        reasons.append(_reason("schedule_unavailable", components["schedule"]["message"]))
    if valid_team_count < 32:
        reasons.append(_reason("team_performance_incomplete", components["team_performance"]["message"]))
    if predictions and len(ready_predictions) < len(predictions):
        reasons.append(_reason("prediction_inputs_missing", components["game_predictions"]["message"]))
    if projection_errors:
        reasons.append(_reason("projection_generation_degraded", components["player_projections"]["message"]))
    elif games and not projection_rows:
        reasons.append(_reason("player_projections_unavailable", components["player_projections"]["message"]))
    if market_errors:
        reasons.append(_reason("market_pricing_degraded", components["market_pricing"]["message"]))
    elif not market_available and not priced_edges:
        reasons.append(_reason("market_pricing_unavailable", components["market_pricing"]["message"]))
    overall_status = (
        "unavailable" if not games and not valid_team_count else ("degraded" if reasons else "ready")
    )

    kpi_status = {
        "win_probability": {
            "status": "ready" if featured else "unavailable",
            "message": None if featured else "No matchup has complete prediction inputs.",
        },
        "prediction_confidence": {
            "status": "ready" if featured else "unavailable",
            "message": None if featured else "Prediction confidence is unavailable.",
        },
        "upside_score": {
            "status": "ready" if market_edge is not None else "unavailable",
            "message": None if market_edge is not None else "No priced projection edge is available.",
        },
        "projected_points": {
            "status": "ready" if featured else "unavailable",
            "message": None if featured else "Projected points are unavailable.",
        },
        "market_edge": {
            "status": "ready" if market_edge is not None else "unavailable",
            "message": None if market_edge is not None else "No priced market edge is available.",
        },
        "data_coverage": {
            "status": _status(valid_team_count, 32),
            "message": components["team_performance"]["message"],
        },
    }

    return {
        "season": season,
        "week": week,
        "season_type": stype,
        "stats_season": ss,
        "generated_at": datetime.now(UTC).isoformat(),
        "kpis": {
            "win_probability": featured["home_prob"] if featured else None,
            "prediction_confidence": featured["confidence"] if featured else None,
            "upside_score": round(max(0, market_edge) * 100, 1) if market_edge is not None else None,
            "projected_points": featured["projected_home"] if featured else None,
            "market_edge": round(market_edge, 4) if market_edge is not None else None,
            "injury_impact": None,
        },
        "kpi_status": kpi_status,
        "featured": featured,
        "upcoming_games": predictions[:5],
        "player_projections": top_players[:8],
        "quick_props": quick_props,
        "team_rankings": rankings[:10],
        "trend": [],
        "data_status": {"status": overall_status, "reasons": reasons, "components": components},
        "engine": {
            "status": overall_status,
            "version": "P3.5 Decision Delivery",
            "data_coverage": round(min(valid_team_count / 32, 1.0), 3),
            "covered_team_count": valid_team_count,
            "expected_team_count": 32,
            "coverage_status": "complete"
            if valid_team_count >= 32
            else ("partial" if valid_team_count else "empty"),
            "source_chain": ["nflverse", "espn"],
            "odds_configured": odds_configured,
            "model_note": (
                "Quick Props is delivered from the P3.4 decision engine. Lean-or-better model picks "
                "surface even when unpriced; Pass rows are watchlist-only and verified price/EV is "
                "required before a model pick becomes actionable."
            ),
        },
    }


@dashboard_bp.route("/api/dashboard")
def api_dashboard():
    cw = nfl_data.current_week()
    season = int(request.args.get("season", cw["season"]))
    week = int(request.args.get("week", cw["week"]))
    stype = request.args.get("type", cw.get("season_type", "REG"))
    return jsonify(_dashboard_payload(season, week, stype))


@dashboard_bp.route("/api/my-hub")
def api_my_hub():
    """Canonical My Hub payload; shares the exact Dashboard decision contract."""
    cw = nfl_data.current_week()
    season = int(request.args.get("season", cw["season"]))
    week = int(request.args.get("week", cw["week"]))
    stype = request.args.get("type", cw.get("season_type", "REG"))
    payload = _dashboard_payload(season, week, stype)
    payload["surface"] = "my-hub"
    return jsonify(payload)
