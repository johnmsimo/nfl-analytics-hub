"""
Tracker API: persistent pick CRUD, CLV/performance, publication ledger,
automatic grading, closing capture, and bankroll settings.
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

import decision_ledger
import tracker
from security import bounded_number, json_body, limiter

tracker_bp = Blueprint("tracker", __name__)


@tracker_bp.route("/api/tracker/pick", methods=["POST"])
@limiter.limit(30, 60, key="user")
def api_add_pick():
    # Must accept the full P3.4-P3.6 decision row posted by the shared slip.
    # P3.7 snapshots these fields at first save and never rewrites the release
    # receipt on duplicate saves.
    allowed = {
        "gameId", "season", "week", "gameday", "playerId", "player", "team",
        "opponent", "position", "marketKey", "marketLabel", "line", "side",
        "price", "book", "stakeDollars", "stakeUnits", "kellyPct", "modelProb",
        "impliedProb", "fairProb", "fairMarketProb", "referenceProb", "edge",
        "evPct", "modelSource", "decisionModelVersion", "source", "modelMean",
        "consensusProb", "simulationProb", "simulationAgreement", "confidenceScore",
        "confidenceGrade", "matchupGrade", "decisionGrade", "decisionScore",
        "decisionReasons", "decisionRisks", "priceStatus", "quoteStatus",
        "bestPrice", "freshBookCount", "pairedFairBookCount", "marketPricing",
        "oddsSnapshotAgeSeconds", "actionable", "evidenceSeason", "rosterVerified",
        # Legacy call sites may still carry these names; retaining them in the
        # request allowlist preserves compatibility even though Tracker does not
        # make them part of the canonical release schema.
        "eventDate", "commenceTime", "projection", "probability",
    }
    payload = json_body(allowed=allowed, required={"marketKey", "side"})
    payload["marketKey"] = str(payload["marketKey"])[:80]
    payload["side"] = str(payload["side"]).lower()[:16]
    if payload["side"] not in {"over", "under", "home", "away", "yes", "no"}:
        return jsonify({"error": "invalid side"}), 400
    if "stakeDollars" in payload:
        payload["stakeDollars"] = bounded_number(payload, "stakeDollars", 0, 1_000_000)
    return jsonify(tracker.add_pick(payload))


@tracker_bp.route("/api/tracker/picks")
def api_list_picks():
    return jsonify(tracker.list_picks(request.args.get("date")))


@tracker_bp.route("/api/tracker/pick/<date>/<pick_id>", methods=["PATCH"])
@limiter.limit(60, 60, key="user")
def api_update_pick(date, pick_id):
    # Release fingerprint/model evidence is intentionally not patchable.
    patch = json_body(
        allowed={
            "grade", "profitDollars", "actual", "closingPrice", "closingImplied",
            "clvEdge", "stakeDollars", "notes", "price", "line", "book",
        }
    )
    out = tracker.update_pick(date, pick_id, patch)
    if out is None:
        return jsonify({"error": "pick not found"}), 404
    return jsonify(out)


@tracker_bp.route("/api/tracker/pick/<date>/<pick_id>", methods=["DELETE"])
@limiter.limit(60, 60, key="user")
def api_delete_pick(date, pick_id):
    if not tracker.delete_pick(date, pick_id):
        return jsonify({"error": "pick not found"}), 404
    return jsonify({"deleted": True})


@tracker_bp.route("/api/tracker/performance")
def api_performance():
    return jsonify(tracker.performance_summary())


@tracker_bp.route("/api/tracker/persistence")
def api_persistence():
    return jsonify(
        {
            "tracker": tracker.persistence_status(),
            "publicationLedger": decision_ledger.ledger_status(),
        }
    )


@tracker_bp.route("/api/tracker/ledger")
def api_ledger():
    season = request.args.get("season")
    week = request.args.get("week")
    limit = int(request.args.get("limit", "250"))
    return jsonify(
        {
            "status": decision_ledger.ledger_status(),
            "receipts": decision_ledger.list_receipts(
                limit=limit,
                season=int(season) if season is not None else None,
                week=int(week) if week is not None else None,
            ),
        }
    )


@tracker_bp.route("/api/tracker/ledger/performance")
def api_ledger_performance():
    return jsonify(decision_ledger.performance_summary())


@tracker_bp.route("/api/tracker/grade", methods=["POST"])
@limiter.limit(10, 60, key="user")
def api_grade():
    tracked = tracker.grade_pending()
    ledger = decision_ledger.grade_pending()
    return jsonify(
        {
            "graded": int(tracked.get("graded", 0)) + int(ledger.get("graded", 0)),
            "trackerGraded": int(tracked.get("graded", 0)),
            "ledgerGraded": int(ledger.get("graded", 0)),
        }
    )


@tracker_bp.route("/api/tracker/ledger/grade", methods=["POST"])
@limiter.limit(10, 60, key="user")
def api_ledger_grade():
    return jsonify(decision_ledger.grade_pending())


@tracker_bp.route("/api/tracker/closing-capture", methods=["POST"])
@limiter.limit(10, 60, key="user")
def api_closing_capture():
    return jsonify(tracker.closing_capture_once())


@tracker_bp.route("/api/tracker/live")
def api_live():
    return jsonify(tracker.live_status())


@tracker_bp.route("/api/tracker/settings", methods=["GET", "POST"])
def api_settings():
    if request.method == "POST":
        payload = json_body(
            allowed={"bankroll", "unit_pct", "kelly_fraction", "max_bet_pct"}
        )
        if "bankroll" in payload:
            payload["bankroll"] = bounded_number(payload, "bankroll", 0, 100_000_000)
        if "unit_pct" in payload:
            payload["unit_pct"] = bounded_number(payload, "unit_pct", 0.001, 0.1)
        if "kelly_fraction" in payload:
            payload["kelly_fraction"] = bounded_number(payload, "kelly_fraction", 0, 1)
        if "max_bet_pct" in payload:
            payload["max_bet_pct"] = bounded_number(payload, "max_bet_pct", 0.001, 1)
        return jsonify(tracker.save_settings(payload))
    return jsonify(tracker.get_settings())
