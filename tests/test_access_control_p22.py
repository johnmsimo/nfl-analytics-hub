import base64
import hashlib
import hmac
import json
import struct

import pytest
from flask import Flask, jsonify

import security
from routes.auth import auth_bp
from security import configure_security


def _totp_code(secret: str, timestamp: float) -> str:
    cleaned = "".join(secret.split()).replace("-", "").upper()
    padding = "=" * ((8 - len(cleaned) % 8) % 8)
    key = base64.b32decode(cleaned + padding, casefold=True)
    counter = int(timestamp // 30)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    binary = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return f"{binary % 1_000_000:06d}"


def _app(monkeypatch, users=None):
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("SECRET_KEY", "p2-2-test-secret-key")
    monkeypatch.setenv("AUTH_DISABLED", "0")
    monkeypatch.delenv("REQUIRE_MFA", raising=False)
    monkeypatch.delenv("ADMIN_TOTP_SECRET", raising=False)
    if users is None:
        monkeypatch.delenv("AUTH_USERS_JSON", raising=False)
        monkeypatch.setenv("ADMIN_USERNAME", "owner")
        monkeypatch.setenv("ADMIN_PASSWORD", "strong-test-password")
        monkeypatch.setenv("ADMIN_DISPLAY_NAME", "Owner")
    else:
        monkeypatch.setenv("AUTH_USERS_JSON", json.dumps(users))

    security.limiter._events.clear()
    app = Flask(__name__)
    app.config["TESTING"] = True
    configure_security(app)
    app.register_blueprint(auth_bp)

    @app.get("/api/admin/overview")
    def admin_overview():
        return jsonify({"ok": True})

    @app.post("/api/admin/warehouse-retention/apply")
    def retention_apply():
        return jsonify({"ok": True})

    @app.post("/api/write")
    def ordinary_write():
        return jsonify({"ok": True})

    @app.get("/settings")
    def settings():
        return "settings"

    return app


def _session(client, role):
    with client.session_transaction() as sess:
        sess["user"] = {"username": role, "name": role.title(), "role": role}
        sess["csrf_token"] = "csrf-test-token"


def test_legacy_single_admin_remains_owner_without_mfa(monkeypatch):
    app = _app(monkeypatch)
    client = app.test_client()

    response = client.post(
        "/api/auth/login",
        json={"username": "owner", "password": "strong-test-password"},
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["mfa_required"] is False
    assert payload["user"]["role"] == "owner"

    session_response = client.get("/api/auth/session")
    assert session_response.status_code == 200
    assert session_response.get_json()["role"] == "owner"


def test_pre_upgrade_legacy_session_is_normalized_to_owner(monkeypatch):
    app = _app(monkeypatch)
    client = app.test_client()

    with client.session_transaction() as sess:
        sess["user"] = {"username": "owner", "name": "Owner"}
        sess["csrf_token"] = "csrf-test-token"

    response = client.get("/api/auth/session")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["role"] == "owner"
    assert payload["user"]["role"] == "owner"


def test_expanded_access_fails_closed_without_mfa_secrets(monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("SECRET_KEY", "p2-2-test-secret-key")
    monkeypatch.setenv(
        "AUTH_USERS_JSON",
        json.dumps(
            {
                "owner": {"role": "owner", "password": "owner-password"},
                "analyst": {"role": "analyst", "password": "analyst-password"},
            }
        ),
    )

    app = Flask(__name__)
    with pytest.raises(RuntimeError, match="MFA is required"):
        configure_security(app)


def test_multi_user_login_requires_and_verifies_totp(monkeypatch):
    secret = "JBSWY3DPEHPK3PXP"
    users = {
        "owner": {
            "name": "Owner",
            "role": "owner",
            "password": "owner-password",
            "totp_secret": secret,
        },
        "analyst": {
            "name": "Analyst",
            "role": "analyst",
            "password": "analyst-password",
            "totp_secret": "KRSXG5DSNFXGOIDB",
        },
    }
    app = _app(monkeypatch, users)
    client = app.test_client()
    timestamp = 1_700_000_000.0
    monkeypatch.setattr(security.time, "time", lambda: timestamp)

    challenge = client.post(
        "/api/auth/login",
        json={"username": "owner", "password": "owner-password"},
    )
    assert challenge.status_code == 202
    assert challenge.get_json()["code"] == "MFA_REQUIRED"

    rejected = client.post(
        "/api/auth/login",
        json={"username": "owner", "password": "owner-password", "otp": "000000"},
    )
    assert rejected.status_code == 401
    assert rejected.get_json()["code"] == "INVALID_MFA"

    accepted = client.post(
        "/api/auth/login",
        json={
            "username": "owner",
            "password": "owner-password",
            "otp": _totp_code(secret, timestamp),
        },
    )
    assert accepted.status_code == 200
    assert accepted.get_json()["user"]["role"] == "owner"


def test_admin_surfaces_require_admin_or_owner(monkeypatch):
    app = _app(monkeypatch)
    client = app.test_client()

    _session(client, "analyst")
    assert client.get("/api/admin/overview").status_code == 403
    assert client.get("/settings").status_code == 403

    _session(client, "admin")
    assert client.get("/api/admin/overview").status_code == 200
    assert client.get("/settings").status_code == 200


def test_p21_apply_operations_are_owner_only(monkeypatch):
    app = _app(monkeypatch)
    client = app.test_client()

    _session(client, "admin")
    denied = client.post(
        "/api/admin/warehouse-retention/apply",
        headers={"X-CSRF-Token": "csrf-test-token"},
    )
    assert denied.status_code == 403
    assert denied.get_json()["code"] == "OWNER_REQUIRED"

    _session(client, "owner")
    allowed = client.post(
        "/api/admin/warehouse-retention/apply",
        headers={"X-CSRF-Token": "csrf-test-token"},
    )
    assert allowed.status_code == 200


def test_viewer_is_read_only_while_analyst_can_write(monkeypatch):
    app = _app(monkeypatch)
    client = app.test_client()

    _session(client, "viewer")
    denied = client.post("/api/write", headers={"X-CSRF-Token": "csrf-test-token"})
    assert denied.status_code == 403
    assert denied.get_json()["code"] == "READ_ONLY_ROLE"

    _session(client, "analyst")
    allowed = client.post("/api/write", headers={"X-CSRF-Token": "csrf-test-token"})
    assert allowed.status_code == 200


def test_invalid_role_configuration_is_rejected(monkeypatch):
    monkeypatch.setenv(
        "AUTH_USERS_JSON",
        json.dumps({"bad": {"role": "superuser", "password": "password"}}),
    )
    with pytest.raises(RuntimeError, match="unsupported auth role"):
        security.configured_auth_users()
