"""ESPN league feeds: injury report + news headlines.

Same source family as the core data layer (nfl_data.py is ESPN-backed);
these are the site feeds the app wasn't consuming yet. Both endpoints
degrade to empty lists — never 5xx because ESPN hiccuped.
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

import nfl_data

feeds_bp = Blueprint("feeds", __name__)


@feeds_bp.route("/api/injuries")
def api_injuries():
    team = (request.args.get("team") or "").upper() or None
    rows = nfl_data.get_injuries()
    if team:
        rows = [r for r in rows if r["team"] == team]
    return jsonify({"injuries": rows, "count": len(rows), "team": team})


@feeds_bp.route("/api/news")
def api_news():
    limit = min(request.args.get("limit", 12, type=int), 30)
    return jsonify({"articles": nfl_data.get_news(limit)})
