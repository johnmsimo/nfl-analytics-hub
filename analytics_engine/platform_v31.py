"""Deterministic v3.1 intelligence services for games, players, teams and markets."""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from math import exp
from typing import Any


def _num(data: Mapping[str, Any], key: str, default: float = 0.0) -> float:
    try:
        return float(data.get(key, default) or default)
    except (TypeError, ValueError):
        return default


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def live_game_center(game: Mapping[str, Any]) -> dict[str, Any]:
    home_score = _num(game, "home_score")
    away_score = _num(game, "away_score")
    seconds = int(_clamp(_num(game, "seconds_remaining", 3600), 0, 3600))
    possession = str(game.get("possession") or "").lower()
    score_diff = home_score - away_score
    possession_edge = 0.45 if possession == "home" else -0.45 if possession == "away" else 0.0
    time_weight = 1.0 + (3600 - seconds) / 900.0
    logit = (score_diff * time_weight / 6.8) + possession_edge
    home_wp = 1.0 / (1.0 + exp(-logit))
    leverage = _clamp((1.0 - abs(home_wp - 0.5) * 2.0) * (1.0 - seconds / 3600.0), 0.0, 1.0)
    state = "final" if seconds == 0 else "live" if seconds < 3600 else "pregame"
    return {
        "state": state,
        "home_win_probability": round(home_wp, 4),
        "away_win_probability": round(1.0 - home_wp, 4),
        "leverage_index": round(leverage, 4),
        "score_differential": round(score_diff, 1),
        "seconds_remaining": seconds,
        "alert": leverage >= 0.72,
    }


def player_intelligence(player: Mapping[str, Any], peers: Iterable[Mapping[str, Any]] = ()) -> dict[str, Any]:
    recent = list(player.get("recent_games") or [])
    values = [_num(game, "fantasy_points") for game in recent]
    baseline = sum(values) / len(values) if values else _num(player, "season_average")
    last3 = sum(values[-3:]) / min(3, len(values)) if values else baseline
    trend = last3 - baseline
    usage = _num(player, "usage_rate")
    matchup = _num(player, "matchup_grade")
    injury = _num(player, "injury_risk")
    projection = max(0.0, baseline + trend * 0.35 + matchup * 0.08 + usage * 2.0 - injury * 3.0)
    peer_rows = []
    for peer in peers:
        distance = abs(_num(peer, "season_average") - baseline) + abs(_num(peer, "usage_rate") - usage) * 5
        peer_rows.append({"name": peer.get("name", "Unknown"), "distance": round(distance, 3)})
    peer_rows.sort(key=lambda row: row["distance"])
    return {
        "player": player.get("name", "Unknown"),
        "team": player.get("team"),
        "position": player.get("position"),
        "projection": round(projection, 2),
        "floor": round(max(0.0, projection * 0.62), 2),
        "ceiling": round(projection * 1.48, 2),
        "trend": "up" if trend > 0.75 else "down" if trend < -0.75 else "steady",
        "trend_value": round(trend, 2),
        "confidence": round(_clamp(62 + len(values) * 3 - injury * 18, 45, 96), 1),
        "similar_players": peer_rows[:5],
    }


def team_intelligence(team: Mapping[str, Any]) -> dict[str, Any]:
    offense = _num(team, "offense_epa")
    defense = -_num(team, "defense_epa_allowed")
    special = _num(team, "special_teams_epa")
    recent = _num(team, "recent_form")
    injury = _num(team, "injury_penalty")
    rating = offense * 42 + defense * 38 + special * 18 + recent * 5 - injury
    strengths = sorted([
        ("offense", offense), ("defense", defense), ("special teams", special), ("recent form", recent / 8)
    ], key=lambda item: item[1], reverse=True)
    return {
        "team": team.get("name") or team.get("team") or "Unknown",
        "power_rating": round(rating, 2),
        "tier": "elite" if rating >= 8 else "contender" if rating >= 3 else "average" if rating >= -3 else "retooling",
        "strengths": [name for name, value in strengths[:2] if value > 0],
        "weaknesses": [name for name, value in reversed(strengths) if value < 0][:2],
        "playoff_probability": round(_clamp(0.5 + rating / 30, 0.02, 0.98), 4),
    }


def betting_intelligence(markets: Iterable[Mapping[str, Any]], bankroll: float = 1000.0) -> dict[str, Any]:
    signals = []
    for market in markets:
        probability = _clamp(_num(market, "model_probability", 0.5), 0.001, 0.999)
        decimal_odds = max(1.01, _num(market, "decimal_odds", 1.91))
        implied = 1.0 / decimal_odds
        edge = probability - implied
        b = decimal_odds - 1.0
        kelly = max(0.0, (b * probability - (1.0 - probability)) / b)
        stake = bankroll * min(kelly * 0.25, 0.025)
        signals.append({
            "market": market.get("market", "Unknown"),
            "selection": market.get("selection", "Unknown"),
            "model_probability": round(probability, 4),
            "implied_probability": round(implied, 4),
            "edge": round(edge, 4),
            "expected_value": round(probability * decimal_odds - 1.0, 4),
            "recommended_stake": round(stake, 2),
            "grade": "A" if edge >= 0.08 else "B" if edge >= 0.05 else "C" if edge >= 0.025 else "pass",
        })
    signals.sort(key=lambda row: row["edge"], reverse=True)
    return {"bankroll": round(bankroll, 2), "signals": signals, "actionable": [row for row in signals if row["grade"] != "pass"]}


def assistant_response(question: str, context: Mapping[str, Any] | None = None) -> dict[str, Any]:
    q = " ".join(question.strip().split())[:500]
    context = context or {}
    if not q:
        return {"answer": "Ask a question about a game, player, team, or market.", "intent": "empty", "confidence": 1.0}
    lower = q.lower()
    intent = "game" if any(x in lower for x in ("game", "win", "matchup")) else "player" if any(x in lower for x in ("player", "yards", "touchdown", "projection")) else "betting" if any(x in lower for x in ("bet", "odds", "spread", "total", "edge")) else "team" if any(x in lower for x in ("team", "ranking", "playoff")) else "general"
    summary = context.get("summary") or context.get("answer")
    answer = str(summary) if summary else f"I classified this as a {intent} question. Add current structured context for a data-grounded answer."
    return {"question": q, "intent": intent, "answer": answer, "confidence": 0.9 if summary else 0.62, "grounded": bool(summary)}


def normalize_watchlist(items: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    unique: dict[tuple[str, str], dict[str, Any]] = {}
    for item in items:
        kind = str(item.get("type") or "team").lower()[:20]
        identifier = str(item.get("id") or item.get("name") or "").strip()[:80]
        if identifier:
            unique[(kind, identifier.lower())] = {"type": kind, "id": identifier, "label": item.get("label") or identifier}
    return {"items": list(unique.values()), "count": len(unique)}