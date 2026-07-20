"""StatMuse-style Q&A endpoint — natural-language stat questions answered
from our own warehouse (stat_query.py). No external service involved."""
from __future__ import annotations

from flask import Blueprint, jsonify, request

import stat_query
from security import limiter

ask_bp = Blueprint("ask", __name__)


@ask_bp.route("/api/ask")
@limiter.limit(60, 60, key="user")
def api_ask():
    q = (request.args.get("q") or "").strip()[:200]
    season = request.args.get("season", type=int)
    ans = stat_query.ask(q, season)
    ans["q"] = q
    return jsonify(ans)


@ask_bp.route("/api/ask/examples")
def api_ask_examples():
    return jsonify({"examples": stat_query.EXAMPLES})
