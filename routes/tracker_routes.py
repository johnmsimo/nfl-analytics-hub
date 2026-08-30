"""
Tracker API: persistent pick CRUD, player-prop and game publication ledgers,
outcome-learning diagnostics, calibration challenger/promotion/guard/control-plane,
market calibration, post-promotion market guard, market control-plane, all-market
calibration portfolio governance and P6.0 audit, automatic grading, closing
capture, and bankroll settings.
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request, session

import decision_ledger
import p38_learning
import p39_calibration
import p44_game_decision_ledger
import p48_game_learning
import p49_game_calibration
import p50_game_calibration_promotion
import p51_game_calibration_guard
import p52_game_calibration_control_plane
import p54_game_market_calibration
import p55_game_market_calibration_guard
import p56_game_market_calibration_control_plane
import p58_calibration_portfolio_control_plane
import p60_calibration_governance_audit
import tracker
from security import bounded_number, json_body, limiter, require_roles

tracker_bp = Blueprint("tracker", __name__)


@tracker_bp.route("/api/tracker/pick", methods=["POST"])
@limiter.limit(30, 60, key="user")
def api_add_pick():
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
        "eventDate", "commenceTime", "projection", "probability",
    }
    payload = json_body(allowed=allowed, required={"marketKey", "side"})
    payload["marketKey"] = str(payload["marketKey"])[:80]
    payload["side"] = str(payload["side"]).lower()[:16]
    if payload["side"] not in {"over", "under", "home", "away", "yes", "no"}:
        return jsonify({"error": "invalid side"}), 400
    if "stakeDollars" in payload:
        payload["stakeDollars"] = bounded_number(payload, "stakeDollars", 0, 1_000_000)
    entry = tracker.add_pick(payload)
    decision_ledger.record_delivery(
        [entry],
        context={
            "source": "tracker_confirmed_pick",
            "season": entry.get("season"),
            "week": entry.get("week"),
            "season_type": payload.get("seasonType") or payload.get("type"),
            "modelVersion": entry.get("modelSource"),
        },
    )
    return jsonify(entry)


@tracker_bp.route("/api/tracker/picks")
def api_list_picks():
    return jsonify(tracker.list_picks(request.args.get("date")))


@tracker_bp.route("/api/tracker/pick/<date>/<pick_id>", methods=["PATCH"])
@limiter.limit(60, 60, key="user")
def api_update_pick(date, pick_id):
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
            "gameDecisionLedger": p44_game_decision_ledger.ledger_status(),
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


@tracker_bp.route("/api/tracker/game-ledger")
def api_game_ledger():
    season = request.args.get("season")
    week = request.args.get("week")
    limit = int(request.args.get("limit", "250"))
    return jsonify(
        {
            "status": p44_game_decision_ledger.ledger_status(),
            "receipts": p44_game_decision_ledger.list_receipts(
                limit=limit,
                season=int(season) if season is not None else None,
                week=int(week) if week is not None else None,
            ),
        }
    )


@tracker_bp.route("/api/tracker/game-ledger/performance")
def api_game_ledger_performance():
    return jsonify(p44_game_decision_ledger.performance_summary())


@tracker_bp.route("/api/game-learning/report")
@tracker_bp.route("/api/tracker/game-learning")
def api_game_learning():
    """P4.8 zero-credit, read-only learning report from immutable game receipts."""
    return jsonify(p48_game_learning.build_learning_report())


@tracker_bp.route("/api/game-calibration/challenger")
@tracker_bp.route("/api/tracker/game-calibration-challenger")
def api_game_calibration_challenger():
    """P4.9 read-only forward-holdout game calibration challenger report."""
    return jsonify(p49_game_calibration.build_production_report())


@tracker_bp.route("/api/game-calibration/champion")
@tracker_bp.route("/api/tracker/game-calibration-champion")
def api_game_calibration_champion():
    """P5.0 read-only champion, promotion-readiness, and immutable event status."""
    return jsonify(p50_game_calibration_promotion.build_status())


@tracker_bp.route("/api/game-calibration/guard")
@tracker_bp.route("/api/tracker/game-calibration-guard")
def api_game_calibration_guard():
    """P5.1 read-only post-promotion champion safety monitor."""
    return jsonify(p51_game_calibration_guard.build_production_report())


@tracker_bp.route("/api/game-calibration/control-plane")
@tracker_bp.route("/api/tracker/game-calibration-control-plane")
def api_game_calibration_control_plane():
    """P5.2 canonical read-only promotion/rollback operating decision."""
    return jsonify(p52_game_calibration_control_plane.build_production_control_plane())


@tracker_bp.route("/api/game-market-calibration/status")
@tracker_bp.route("/api/tracker/game-market-calibration")
def api_game_market_calibration_status():
    """P5.4 market-isolated spread/total calibration governance status."""
    return jsonify(p54_game_market_calibration.build_production_report())


@tracker_bp.route("/api/game-market-calibration/guard")
@tracker_bp.route("/api/tracker/game-market-calibration-guard")
def api_game_market_calibration_guard():
    """P5.5 read-only spread/total post-promotion champion safety monitor."""
    return jsonify(p55_game_market_calibration_guard.build_production_report())


@tracker_bp.route("/api/game-market-calibration/control-plane")
@tracker_bp.route("/api/tracker/game-market-calibration-control-plane")
def api_game_market_calibration_control_plane():
    """P5.6 canonical read-only spread/total promotion and rollback decision."""
    return jsonify(p56_game_market_calibration_control_plane.build_production_control_plane())


@tracker_bp.route("/api/game-calibration/portfolio-control-plane")
@tracker_bp.route("/api/tracker/game-calibration-portfolio-control-plane")
def api_game_calibration_portfolio_control_plane():
    """P5.8 canonical read-only moneyline/spread/total governance portfolio."""
    return jsonify(p58_calibration_portfolio_control_plane.build_production_portfolio())


@tracker_bp.route("/api/game-calibration/audit-ledger")
@tracker_bp.route("/api/tracker/game-calibration-audit-ledger")
def api_game_calibration_audit_ledger():
    """P6.0 read-only unified audit of P5.0/P5.4 calibration governance history."""
    return jsonify(p60_calibration_governance_audit.build_production_report())


@tracker_bp.route("/api/game-calibration/promote", methods=["POST"])
@tracker_bp.route("/api/tracker/game-calibration-promote", methods=["POST"])
@limiter.limit(5, 60, key="user")
@require_roles("owner")
def api_game_calibration_promote():
    payload = json_body(
        allowed={"candidateId", "confirmation"},
        required={"candidateId", "confirmation"},
    )
    actor = str((session.get("user") or {}).get("username") or "owner")
    result = p50_game_calibration_promotion.promote_candidate(
        str(payload["candidateId"]),
        confirmation=str(payload["confirmation"]),
        actor=actor,
    )
    if result.get("ok"):
        return jsonify(result)
    code = result.get("code")
    return jsonify(result), 409 if code == "PROMOTION_GATE_FAILED" else 400


@tracker_bp.route("/api/game-calibration/rollback", methods=["POST"])
@tracker_bp.route("/api/tracker/game-calibration-rollback", methods=["POST"])
@limiter.limit(5, 60, key="user")
@require_roles("owner")
def api_game_calibration_rollback():
    payload = json_body(allowed={"confirmation"}, required={"confirmation"})
    actor = str((session.get("user") or {}).get("username") or "owner")
    result = p50_game_calibration_promotion.rollback_to_baseline(
        confirmation=str(payload["confirmation"]),
        actor=actor,
    )
    return (jsonify(result), 200) if result.get("ok") else (jsonify(result), 400)


@tracker_bp.route("/api/game-market-calibration/promote", methods=["POST"])
@tracker_bp.route("/api/tracker/game-market-calibration-promote", methods=["POST"])
@limiter.limit(5, 60, key="user")
@require_roles("owner")
def api_game_market_calibration_promote():
    payload = json_body(
        allowed={"market", "candidateId", "confirmation"},
        required={"market", "candidateId", "confirmation"},
    )
    actor = str((session.get("user") or {}).get("username") or "owner")
    try:
        result = p54_game_market_calibration.promote_candidate(
            str(payload["market"]),
            str(payload["candidateId"]),
            confirmation=str(payload["confirmation"]),
            actor=actor,
        )
    except ValueError as exc:
        return jsonify({"ok": False, "code": "INVALID_MARKET", "error": str(exc)}), 400
    if result.get("ok"):
        return jsonify(result)
    return jsonify(result), 409 if result.get("code") == "PROMOTION_GATE_FAILED" else 400


@tracker_bp.route("/api/game-market-calibration/rollback", methods=["POST"])
@tracker_bp.route("/api/tracker/game-market-calibration-rollback", methods=["POST"])
@limiter.limit(5, 60, key="user")
@require_roles("owner")
def api_game_market_calibration_rollback():
    payload = json_body(
        allowed={"market", "confirmation"},
        required={"market", "confirmation"},
    )
    actor = str((session.get("user") or {}).get("username") or "owner")
    try:
        result = p54_game_market_calibration.rollback_to_baseline(
            str(payload["market"]),
            confirmation=str(payload["confirmation"]),
            actor=actor,
        )
    except ValueError as exc:
        return jsonify({"ok": False, "code": "INVALID_MARKET", "error": str(exc)}), 400
    return (jsonify(result), 200) if result.get("ok") else (jsonify(result), 400)


@tracker_bp.route("/api/tracker/learning")
def api_learning():
    return jsonify(p38_learning.build_learning_report())


@tracker_bp.route("/api/tracker/calibration-challenger")
def api_calibration_challenger():
    return jsonify(p39_calibration.build_production_report())


@tracker_bp.route("/api/tracker/grade", methods=["POST"])
@limiter.limit(10, 60, key="user")
def api_grade():
    tracked = tracker.grade_pending()
    ledger = decision_ledger.grade_pending()
    game_ledger = p44_game_decision_ledger.grade_pending()
    return jsonify(
        {
            "graded": int(tracked.get("graded", 0))
            + int(ledger.get("graded", 0))
            + int(game_ledger.get("graded", 0)),
            "trackerGraded": int(tracked.get("graded", 0)),
            "ledgerGraded": int(ledger.get("graded", 0)),
            "gameLedgerGraded": int(game_ledger.get("graded", 0)),
        }
    )


@tracker_bp.route("/api/tracker/ledger/grade", methods=["POST"])
@limiter.limit(10, 60, key="user")
def api_ledger_grade():
    return jsonify(decision_ledger.grade_pending())


@tracker_bp.route("/api/tracker/game-ledger/grade", methods=["POST"])
@limiter.limit(10, 60, key="user")
def api_game_ledger_grade():
    return jsonify(p44_game_decision_ledger.grade_pending())


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
