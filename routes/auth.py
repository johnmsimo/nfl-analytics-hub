from __future__ import annotations

from urllib.parse import urlparse

from flask import Blueprint, jsonify, session

from security import (
    authenticate_user,
    establish_session,
    json_body,
    limiter,
    user_requires_mfa,
    verify_user_totp,
)

auth_bp = Blueprint("auth", __name__)


def _safe_next(value: str | None) -> str:
    if not value:
        return "/"
    parsed = urlparse(value)
    return value if not parsed.netloc and value.startswith("/") else "/"


@auth_bp.route("/api/auth/login", methods=["POST"])
@limiter.limit(5, 60)
def api_login():
    payload = json_body(
        allowed={"username", "password", "otp", "next"},
        required={"username", "password"},
    )
    username = str(payload["username"])[:128]
    password = str(payload["password"])[:512]
    user_record = authenticate_user(username, password)
    if user_record is None:
        return jsonify({"error": "invalid credentials", "code": "INVALID_CREDENTIALS"}), 401

    if user_requires_mfa(user_record):
        otp = str(payload.get("otp") or "")[:32]
        if not otp:
            return (
                jsonify(
                    {
                        "ok": False,
                        "mfa_required": True,
                        "code": "MFA_REQUIRED",
                    }
                ),
                202,
            )
        if not verify_user_totp(user_record, otp):
            return jsonify({"error": "invalid verification code", "code": "INVALID_MFA"}), 401

    user = establish_session(user_record)
    return jsonify(
        {
            "ok": True,
            "mfa_required": False,
            "user": user,
            "csrf_token": session["csrf_token"],
            "next": _safe_next(payload.get("next")),
        }
    )


@auth_bp.route("/api/auth/session")
def api_session():
    return jsonify(
        {
            "authenticated": True,
            "user": session["user"],
            "role": (session.get("user") or {}).get("role", "viewer"),
            "csrf_token": session["csrf_token"],
            "enterprise_tenant": session.get("enterprise_tenant"),
        }
    )


@auth_bp.route("/api/auth/logout", methods=["POST"])
def api_logout():
    session.clear()
    return jsonify({"ok": True})
