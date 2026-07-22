"""Authentication, CSRF, distributed rate limiting, and security headers."""
from __future__ import annotations

import hmac
import json
import logging
import os
import secrets
import threading
import time
import uuid
from collections import defaultdict, deque
from functools import wraps
from typing import Any, Callable

from flask import abort, g, jsonify, make_response, redirect, request, session, url_for

try:
    import redis
except ImportError:  # pragma: no cover
    redis = None

_MUTATING = {"POST", "PUT", "PATCH", "DELETE"}
_PUBLIC_ENDPOINTS = {"login", "api_login", "health", "ready", "static"}


def _is_production() -> bool:
    return os.getenv("APP_ENV", os.getenv("FLASK_ENV", "development")).lower() == "production"


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
        if request.endpoint in _PUBLIC_ENDPOINTS or request.path.startswith("/static/") or request.path == "/api/auth/login":
            return None
        if os.getenv("AUTH_DISABLED", "0") == "1" and not _is_production():
            session.setdefault("user", {"username": "developer", "name": "Developer"})
            session.setdefault("csrf_token", secrets.token_urlsafe(32))
            return None
        if not session.get("user"):
            if request.path.startswith("/api/"):
                return jsonify({"error": "authentication required", "code": "AUTH_REQUIRED"}), 401
            return redirect(url_for("login", next=request.full_path.rstrip("?")))
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


def authenticate(username: str, password: str) -> bool:
    expected_user = os.getenv("ADMIN_USERNAME", "admin")
    expected_pass = os.getenv("ADMIN_PASSWORD")
    if not expected_pass:
        if _is_production():
            return False
        expected_pass = "nfl-dev"
    return hmac.compare_digest(username, expected_user) and hmac.compare_digest(password, expected_pass)


def establish_session(username: str) -> dict[str, str]:
    user = {"username": username, "name": os.getenv("ADMIN_DISPLAY_NAME", username.title())}
    session.clear()
    session.permanent = True
    session["user"] = user
    session["csrf_token"] = secrets.token_urlsafe(32)
    return user


def json_body(*, allowed: set[str] | None = None, required: set[str] | None = None) -> dict[str, Any]:
    if not request.is_json:
        abort(make_response(jsonify({"error": "Content-Type must be application/json", "code": "INVALID_CONTENT_TYPE"}), 415))
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        abort(make_response(jsonify({"error": "JSON object required", "code": "INVALID_JSON"}), 400))
    if allowed is not None:
        unknown = sorted(set(payload) - allowed)
        if unknown:
            abort(make_response(jsonify({"error": f"unknown fields: {', '.join(unknown)}", "code": "UNKNOWN_FIELDS"}), 400))
    missing = sorted((required or set()) - set(payload))
    if missing:
        abort(make_response(jsonify({"error": f"missing fields: {', '.join(missing)}", "code": "MISSING_FIELDS"}), 400))
    return payload


def bounded_number(payload: dict[str, Any], key: str, low: float, high: float, *, required: bool = False) -> float | None:
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
                client = redis.Redis.from_url(url, decode_responses=True, socket_connect_timeout=1, socket_timeout=1)
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
                    resp = jsonify({"error": "rate limit exceeded", "code": "RATE_LIMITED", "retry_after": retry})
                    resp.status_code = 429
                    resp.headers["Retry-After"] = str(retry)
                    return resp
                return fn(*args, **kwargs)
            return wrapped
        return decorator


limiter = RateLimiter()
