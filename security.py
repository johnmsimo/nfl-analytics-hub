"""Authentication, MFA, role authorization, CSRF, rate limiting, and security headers."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import logging
import os
import secrets
import struct
import threading
import time
import uuid
from collections import defaultdict, deque
from collections.abc import Callable
from functools import wraps
from typing import Any

from flask import abort, g, jsonify, make_response, redirect, request, session, url_for
from werkzeug.security import check_password_hash

try:
    import redis
except ImportError:  # pragma: no cover
    redis = None

_MUTATING = {"POST", "PUT", "PATCH", "DELETE"}
_PUBLIC_ENDPOINTS = {"login", "api_login", "health", "ready", "static"}
_ROLES = {"viewer", "analyst", "admin", "owner"}
_ADMIN_ROLES = {"admin", "owner"}
_ADMIN_PAGE_PATHS = {"/settings", "/admin/data", "/model-operations", "/enterprise-operations"}
_OWNER_ONLY_MUTATIONS = {
    "/api/admin/player-identities/reconcile",
    "/api/admin/warehouse-retention/apply",
}


def _is_production() -> bool:
    return os.getenv("APP_ENV", os.getenv("FLASK_ENV", "development")).lower() == "production"


def _truthy_env(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _legacy_auth_user() -> dict[str, dict[str, str]]:
    username = os.getenv("ADMIN_USERNAME", "admin").strip() or "admin"
    password = os.getenv("ADMIN_PASSWORD")
    if not password:
        if _is_production():
            password = ""
        else:
            password = "nfl-dev"
    return {
        username: {
            "username": username,
            "name": os.getenv("ADMIN_DISPLAY_NAME", username.title()),
            "role": "owner",
            "password": password,
            "password_hash": "",
            "totp_secret": os.getenv("ADMIN_TOTP_SECRET", "").strip(),
        }
    }


def configured_auth_users() -> dict[str, dict[str, str]]:
    """Return validated auth records without ever logging their secrets."""
    raw = os.getenv("AUTH_USERS_JSON", "").strip()
    if not raw:
        return _legacy_auth_user()
    if len(raw) > 65536:
        raise RuntimeError("AUTH_USERS_JSON exceeds the 64 KiB safety limit")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("AUTH_USERS_JSON must contain valid JSON") from exc

    if isinstance(parsed, dict):
        records = []
        for username, value in parsed.items():
            if not isinstance(value, dict):
                raise RuntimeError("AUTH_USERS_JSON user entries must be objects")
            records.append({"username": username, **value})
    elif isinstance(parsed, list):
        records = parsed
    else:
        raise RuntimeError("AUTH_USERS_JSON must be an object or array")

    if not records or len(records) > 50:
        raise RuntimeError("AUTH_USERS_JSON must define between 1 and 50 users")

    users: dict[str, dict[str, str]] = {}
    for item in records:
        if not isinstance(item, dict):
            raise RuntimeError("AUTH_USERS_JSON user entries must be objects")
        username = str(item.get("username", "")).strip()
        if not username or len(username) > 128:
            raise RuntimeError("every auth user needs a username of 1-128 characters")
        if username in users:
            raise RuntimeError("AUTH_USERS_JSON contains a duplicate username")

        role = str(item.get("role", "viewer")).strip().lower()
        if role not in _ROLES:
            raise RuntimeError(f"unsupported auth role for {username}")

        password = item.get("password")
        password_hash = item.get("password_hash")
        if bool(password) == bool(password_hash):
            raise RuntimeError(f"{username} must define exactly one of password or password_hash")

        users[username] = {
            "username": username,
            "name": str(item.get("name") or username),
            "role": role,
            "password": str(password or ""),
            "password_hash": str(password_hash or ""),
            "totp_secret": str(item.get("totp_secret") or "").strip(),
        }
    return users


def validate_auth_configuration() -> dict[str, dict[str, str]]:
    users = configured_auth_users()
    require_mfa = _truthy_env("REQUIRE_MFA") or len(users) > 1
    if require_mfa:
        missing = [username for username, user in users.items() if not user["totp_secret"]]
        if missing:
            raise RuntimeError("MFA is required for every configured user when access expands beyond one account")
    return users


def authenticate_user(username: str, password: str) -> dict[str, str] | None:
    users = configured_auth_users()
    user = users.get(username)
    if user is None:
        hmac.compare_digest(password, secrets.token_hex(16))
        return None

    password_hash = user["password_hash"]
    if password_hash:
        try:
            valid = check_password_hash(password_hash, password)
        except ValueError:
            valid = False
    else:
        expected = user["password"]
        valid = bool(expected) and hmac.compare_digest(password, expected)
    return user if valid else None


def authenticate(username: str, password: str) -> bool:
    """Backward-compatible boolean credential check."""
    return authenticate_user(username, password) is not None


def user_requires_mfa(user: dict[str, str]) -> bool:
    users = configured_auth_users()
    return bool(user.get("totp_secret")) or _truthy_env("REQUIRE_MFA") or len(users) > 1


def verify_totp(secret: str, code: str, *, now: float | None = None, window: int = 1) -> bool:
    code = str(code).strip()
    if len(code) != 6 or not code.isdigit():
        return False
    cleaned = "".join(secret.split()).replace("-", "").upper()
    if not cleaned:
        return False
    padding = "=" * ((8 - len(cleaned) % 8) % 8)
    try:
        key = base64.b32decode(cleaned + padding, casefold=True)
    except (binascii.Error, ValueError):
        return False
    if len(key) < 10:
        return False

    timestamp = time.time() if now is None else now
    counter = int(timestamp // 30)
    for offset in range(-window, window + 1):
        candidate_counter = counter + offset
        if candidate_counter < 0:
            continue
        digest = hmac.new(key, struct.pack(">Q", candidate_counter), hashlib.sha1).digest()
        dynamic_offset = digest[-1] & 0x0F
        binary = struct.unpack(">I", digest[dynamic_offset : dynamic_offset + 4])[0] & 0x7FFFFFFF
        candidate = f"{binary % 1_000_000:06d}"
        if hmac.compare_digest(candidate, code):
            return True
    return False


def verify_user_totp(user: dict[str, str], code: str) -> bool:
    return verify_totp(user.get("totp_secret", ""), code)


def current_role() -> str:
    user = session.get("user") or {}
    role = str(user.get("role") or "").lower()
    if role in _ROLES:
        return role
    if user.get("username") == os.getenv("ADMIN_USERNAME", "admin"):
        return "owner"
    return "viewer"


def require_roles(*roles: str) -> Callable:
    allowed = set(roles)
    if not allowed or not allowed.issubset(_ROLES):
        raise ValueError("require_roles received an unsupported role")

    def decorator(fn: Callable) -> Callable:
        @wraps(fn)
        def wrapped(*args, **kwargs):
            role = current_role()
            if role not in allowed:
                return (
                    jsonify(
                        {
                            "error": "insufficient role",
                            "code": "ROLE_REQUIRED",
                            "required_roles": sorted(allowed),
                        }
                    ),
                    403,
                )
            return fn(*args, **kwargs)

        return wrapped

    return decorator


def _configure_logging(app) -> None:
    level = getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO)
    app.logger.setLevel(level)

    @app.before_request
    def _request_context():
        g.request_started_at = time.monotonic()
        supplied = request.headers.get("X-Request-ID", "")
        g.request_id = supplied[:128] if supplied else uuid.uuid4().hex

    @app.after_request
    def _request_log(resp):
        duration_ms = round((time.monotonic() - getattr(g, "request_started_at", time.monotonic())) * 1000, 1)
        resp.headers.setdefault("X-Request-ID", getattr(g, "request_id", "unknown"))
        event = {
            "event": "http_request",
            "request_id": getattr(g, "request_id", None),
            "method": request.method,
            "path": request.path,
            "status": resp.status_code,
            "duration_ms": duration_ms,
            "remote_addr": request.headers.get("Fly-Client-IP") or request.remote_addr,
        }
        app.logger.info(json.dumps(event, separators=(",", ":")))
        return resp


def configure_security(app) -> None:
    secret = os.getenv("SECRET_KEY")
    if not secret:
        if _is_production():
            raise RuntimeError("SECRET_KEY is required in production")
        secret = "dev-only-change-me-" + secrets.token_hex(16)

    validate_auth_configuration()
    app.config.update(
        SECRET_KEY=secret,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=_is_production(),
        PERMANENT_SESSION_LIFETIME=60 * 60 * 12,
        MAX_CONTENT_LENGTH=int(os.getenv("MAX_CONTENT_LENGTH", str(1024 * 1024))),
    )
    _configure_logging(app)

    @app.before_request
    def _auth_and_csrf():
        if request.endpoint is None:
            return None
        g.enterprise_api_key = None
        supplied_api_key = request.headers.get("X-API-Key")
        if supplied_api_key:
            if not (
                request.path.startswith("/api/v4.4/")
                or request.path.startswith("/api/v4.5/")
            ):
                return (
                    jsonify(
                        {
                            "error": "API keys are limited to v4.4 enterprise routes",
                            "code": "API_KEY_ROUTE_UNSUPPORTED",
                        }
                    ),
                    401,
                )
            try:
                from enterprise_identity_v441 import authenticate_api_key

                g.enterprise_api_key = authenticate_api_key(supplied_api_key)
            except (PermissionError, RuntimeError, ValueError):
                return (
                    jsonify(
                        {
                            "error": "invalid or inactive API credential",
                            "code": "INVALID_API_KEY",
                        }
                    ),
                    401,
                )
            return None
        if (
            request.endpoint in _PUBLIC_ENDPOINTS
            or request.path.startswith("/static/")
            or request.path == "/api/auth/login"
        ):
            return None
        if os.getenv("AUTH_DISABLED", "0") == "1" and not _is_production():
            session.setdefault(
                "user",
                {"username": "developer", "name": "Developer", "role": "owner"},
            )
            session.setdefault("csrf_token", secrets.token_urlsafe(32))
            return None
        if not session.get("user"):
            if request.path.startswith("/api/"):
                return jsonify({"error": "authentication required", "code": "AUTH_REQUIRED"}), 401
            return redirect(url_for("login", next=request.full_path.rstrip("?")))

        role = current_role()
        if request.path.startswith("/api/admin/") or request.path in _ADMIN_PAGE_PATHS:
            if role not in _ADMIN_ROLES:
                if request.path.startswith("/api/"):
                    return jsonify({"error": "administrator role required", "code": "ROLE_REQUIRED"}), 403
                abort(403)
        if request.path in _OWNER_ONLY_MUTATIONS and request.method in _MUTATING and role != "owner":
            return jsonify({"error": "owner role required", "code": "OWNER_REQUIRED"}), 403

        if role == "viewer" and request.method in _MUTATING and request.path != "/api/auth/logout":
            return jsonify({"error": "viewer role is read-only", "code": "READ_ONLY_ROLE"}), 403

        if request.method in _MUTATING:
            expected = session.get("csrf_token")
            supplied = request.headers.get("X-CSRF-Token")
            if not expected or not supplied or not hmac.compare_digest(expected, supplied):
                return jsonify({"error": "invalid or missing CSRF token", "code": "CSRF_FAILED"}), 403
        return None

    @app.after_request
    def _security_headers(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        resp.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        resp.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "img-src 'self' data: https:; connect-src 'self'; "
            "font-src 'self' data: https://fonts.gstatic.com; frame-ancestors 'none'",
        )
        if _is_production():
            resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        if request.path.startswith("/api/"):
            resp.headers.setdefault("Cache-Control", "no-store")
        return resp


def establish_session(user: str | dict[str, str]) -> dict[str, str]:
    if isinstance(user, str):
        record = configured_auth_users().get(user) or {
            "username": user,
            "name": os.getenv("ADMIN_DISPLAY_NAME", user.title()),
            "role": "owner",
        }
    else:
        record = user
    public_user = {
        "username": str(record["username"]),
        "name": str(record.get("name") or record["username"]),
        "role": str(record.get("role") or "viewer"),
    }
    session.clear()
    session.permanent = True
    session["user"] = public_user
    session["csrf_token"] = secrets.token_urlsafe(32)
    return public_user


def json_body(*, allowed: set[str] | None = None, required: set[str] | None = None) -> dict[str, Any]:
    if not request.is_json:
        abort(
            make_response(
                jsonify({"error": "Content-Type must be application/json", "code": "INVALID_CONTENT_TYPE"}),
                415,
            )
        )
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        abort(make_response(jsonify({"error": "JSON object required", "code": "INVALID_JSON"}), 400))
    if allowed is not None:
        unknown = sorted(set(payload) - allowed)
        if unknown:
            abort(
                make_response(
                    jsonify({"error": f"unknown fields: {', '.join(unknown)}", "code": "UNKNOWN_FIELDS"}), 400
                )
            )
    missing = sorted((required or set()) - set(payload))
    if missing:
        abort(
            make_response(
                jsonify({"error": f"missing fields: {', '.join(missing)}", "code": "MISSING_FIELDS"}), 400
            )
        )
    return payload


def bounded_number(
    payload: dict[str, Any], key: str, low: float, high: float, *, required: bool = False
) -> float | None:
    if key not in payload:
        if required:
            abort(make_response(jsonify({"error": f"{key} is required"}), 400))
        return None
    value = payload[key]
    if isinstance(value, bool):
        abort(make_response(jsonify({"error": f"{key} must be a number"}), 400))
    try:
        num = float(value)
    except (TypeError, ValueError):
        abort(make_response(jsonify({"error": f"{key} must be a number"}), 400))
    if not low <= num <= high:
        abort(make_response(jsonify({"error": f"{key} must be between {low} and {high}"}), 400))
    return num


class RateLimiter:
    """Redis fixed-window limiter with an in-memory sliding-window fallback."""

    def __init__(self) -> None:
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()
        self._redis = None
        url = os.getenv("REDIS_URL")
        if url and redis is not None:
            try:
                client = redis.Redis.from_url(
                    url, decode_responses=True, socket_connect_timeout=1, socket_timeout=1
                )
                client.ping()
                self._redis = client
            except Exception:
                self._redis = None

    @property
    def backend_name(self) -> str:
        return "redis" if self._redis is not None else "memory"

    def _allowed(self, bucket: str, count: int, seconds: int) -> tuple[bool, int]:
        if self._redis is not None:
            redis_key = f"rate-limit:{bucket}:{int(time.time() // seconds)}"
            try:
                current = self._redis.incr(redis_key)
                if current == 1:
                    self._redis.expire(redis_key, seconds + 1)
                return current <= count, max(1, self._redis.ttl(redis_key))
            except Exception:
                pass
        now = time.monotonic()
        with self._lock:
            q = self._events[bucket]
            cutoff = now - seconds
            while q and q[0] <= cutoff:
                q.popleft()
            if len(q) >= count:
                return False, max(1, int(seconds - (now - q[0])))
            q.append(now)
        return True, seconds

    def limit(self, count: int, seconds: int, key: str = "ip") -> Callable:
        def decorator(fn: Callable) -> Callable:
            @wraps(fn)
            def wrapped(*args, **kwargs):
                identity = request.headers.get("Fly-Client-IP") or request.remote_addr or "unknown"
                if key == "user" and session.get("user"):
                    identity = session["user"].get("username", identity)
                bucket = f"{request.endpoint}:{identity}"
                allowed, retry = self._allowed(bucket, count, seconds)
                if not allowed:
                    resp = jsonify(
                        {"error": "rate limit exceeded", "code": "RATE_LIMITED", "retry_after": retry}
                    )
                    resp.status_code = 429
                    resp.headers["Retry-After"] = str(retry)
                    return resp
                return fn(*args, **kwargs)

            return wrapped

        return decorator


limiter = RateLimiter()
