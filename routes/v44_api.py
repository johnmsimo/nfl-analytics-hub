"""NFL Analytics Hub v4.4 enterprise access endpoints."""

from __future__ import annotations

import math
import time
from typing import Any

from flask import Blueprint, Response, g, jsonify, request, session

from ai_decision_v4 import decision_brief, ensemble_decision, scenario_decision
from db_models import EnterpriseApiKey
from enterprise_identity_v441 import (
    authorize_context,
    bind_tenant,
    create_organization,
    issue_api_key,
    list_api_keys,
    register_membership,
    revoke_api_key,
    rotate_api_key,
)
from enterprise_quota_v442 import (
    IdempotencyConflictError,
    QuotaExceededError,
    get_quota_backend,
    normalize_quota_policy,
    quota_manifest,
    request_digest,
)
from enterprise_v44 import (
    authorize_access,
    enterprise_manifest,
    normalize_membership,
    normalize_organization,
    role_catalog,
)

v44_bp = Blueprint("v44_api", __name__, url_prefix="/api/v4.4")
MAX_PUBLIC_MODELS = 100
MAX_PUBLIC_SCENARIOS = 100


def _json_object() -> dict[str, Any] | None:
    payload = request.get_json(silent=True)
    return payload if isinstance(payload, dict) else None


def _session_subject() -> dict[str, str] | None:
    user = session.get("user")
    if not isinstance(user, dict):
        return None
    identifier = user.get("username") or user.get("id") or user.get("email")
    normalized = str(identifier or "").strip().lower()
    return {"type": "user", "id": normalized} if normalized else None


def _request_context() -> dict[str, Any] | None:
    api_key_context = getattr(g, "enterprise_api_key", None)
    if isinstance(api_key_context, dict):
        return api_key_context
    tenant = session.get("enterprise_tenant")
    return tenant if isinstance(tenant, dict) else None


def _authorized(
    organization_id: str,
    permission: str,
) -> tuple[dict[str, Any] | None, tuple[Any, int] | None]:
    context = _request_context()
    if context is None:
        return None, (
            jsonify(
                {
                    "error": "enterprise tenant context required",
                    "code": "TENANT_CONTEXT_REQUIRED",
                }
            ),
            403,
        )
    try:
        authorize_context(context, organization_id, permission)
    except (KeyError, PermissionError, ValueError) as exc:
        return None, (
            jsonify({"error": str(exc), "code": "ENTERPRISE_ACCESS_DENIED"}),
            403,
        )
    return context, None


def _quota_headers(response: Response, quota: dict[str, Any]) -> Response:
    organization = quota["organization"]
    credential = quota["credential"]
    effective_limit = min(organization["limit"], credential["limit"])
    effective_remaining = min(organization["remaining"], credential["remaining"])
    response.headers["RateLimit-Limit"] = str(effective_limit)
    response.headers["RateLimit-Remaining"] = str(effective_remaining)
    response.headers["RateLimit-Reset"] = str(quota["reset_at"])
    response.headers["X-RateLimit-Organization-Limit"] = str(organization["limit"])
    response.headers["X-RateLimit-Organization-Remaining"] = str(organization["remaining"])
    response.headers["X-RateLimit-Credential-Limit"] = str(credential["limit"])
    response.headers["X-RateLimit-Credential-Remaining"] = str(credential["remaining"])
    if quota.get("replayed"):
        response.headers["Idempotent-Replay"] = "true"
    return response


def _quota_unavailable(exc: RuntimeError) -> tuple[Any, int]:
    return (
        jsonify(
            {
                "error": str(exc),
                "code": "QUOTA_BACKEND_UNAVAILABLE",
                "retryable": True,
            }
        ),
        503,
    )


def _public_api_context() -> tuple[dict[str, Any] | None, tuple[Any, int] | None]:
    context = getattr(g, "enterprise_api_key", None)
    if not isinstance(context, dict):
        return None, (
            jsonify(
                {
                    "error": "a scoped v4.4 API key is required",
                    "code": "API_KEY_REQUIRED",
                }
            ),
            401,
        )
    _, denied = _authorized(context["organization_id"], "decision.execute")
    return (None, denied) if denied is not None else (context, None)


def _meter_public_decision(
    operation: str,
    payload: dict[str, Any],
) -> tuple[dict[str, Any] | None, tuple[Any, int] | None]:
    context, denied = _public_api_context()
    if denied is not None:
        return None, denied
    if context is None:
        return None, (jsonify({"error": "API key context required"}), 401)
    idempotency_key = request.headers.get("Idempotency-Key")
    try:
        digest = request_digest(operation, payload)
        quota = get_quota_backend().consume(
            context["organization_id"],
            context["api_key_id"],
            operation,
            idempotency_key,
            digest,
        )
    except IdempotencyConflictError as exc:
        return None, (
            jsonify({"error": str(exc), "code": "IDEMPOTENCY_CONFLICT"}),
            409,
        )
    except QuotaExceededError as exc:
        response = jsonify(
            {
                "error": "enterprise decision quota exceeded",
                "code": "QUOTA_EXCEEDED",
                "quota": exc.decision,
            }
        )
        response.status_code = 429
        response.headers["Retry-After"] = str(exc.decision["retry_after_seconds"])
        response.headers["X-RateLimit-Exceeded-Scope"] = str(exc.decision["exceeded_scope"])
        return None, (_quota_headers(response, exc.decision), 429)
    except ValueError as exc:
        return None, (jsonify({"error": str(exc), "code": "INVALID_REQUEST"}), 400)
    except RuntimeError as exc:
        return None, _quota_unavailable(exc)
    return quota, None


def _public_response(
    operation: str,
    result: dict[str, Any],
    quota: dict[str, Any],
) -> Response:
    response = jsonify(
        {
            "version": "4.4.2",
            "operation": operation,
            "request_id": quota["idempotency_key"],
            "result": result,
            "quota": quota,
        }
    )
    return _quota_headers(response, quota)


def _bounded_object_list(
    value: Any,
    field: str,
    maximum: int,
) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise ValueError(f"{field} must be a list")
    if len(value) > maximum:
        raise ValueError(f"{field} cannot contain more than {maximum} items")
    if any(not isinstance(item, dict) for item in value):
        raise ValueError(f"each {field} item must be a JSON object")
    return value


def _finite_number(value: Any, field: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a finite number") from exc
    if not math.isfinite(result):
        raise ValueError(f"{field} must be a finite number")
    return result


@v44_bp.get("/capabilities")
def capabilities():
    manifest = enterprise_manifest()
    return jsonify(
        {
            **manifest,
            "endpoints": {
                "capabilities": "/api/v4.4/capabilities",
                "roles": "/api/v4.4/access/roles",
                "organization_normalize": "/api/v4.4/organizations/normalize",
                "membership_normalize": "/api/v4.4/memberships/normalize",
                "access_authorize": "/api/v4.4/access/authorize",
                "organization_persist": "/api/v4.4/directory/organizations",
                "membership_persist": ("/api/v4.4/directory/organizations/{organization_id}/memberships"),
                "tenant_session": "/api/v4.4/session/tenant",
                "api_keys": ("/api/v4.4/directory/organizations/{organization_id}/api-keys"),
                "quotas": ("/api/v4.4/directory/organizations/{organization_id}/quotas"),
                "usage": ("/api/v4.4/directory/organizations/{organization_id}/usage"),
                "public_decision_ensemble": "/api/v4.4/public/decisions/ensemble",
                "public_decision_scenario": "/api/v4.4/public/decisions/scenario",
                "public_decision_brief": "/api/v4.4/public/decisions/brief",
            },
            "quota_contract": quota_manifest(),
        }
    )


@v44_bp.get("/access/roles")
def access_roles():
    return jsonify({"version": "4.4.1", "roles": role_catalog()})


@v44_bp.post("/organizations/normalize")
def normalize_enterprise_organization():
    payload = _json_object()
    if payload is None:
        return jsonify({"error": "organization must be a JSON object"}), 400
    try:
        result = normalize_organization(payload, created_at=payload.get("created_at"))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(result)


@v44_bp.post("/memberships/normalize")
def normalize_enterprise_membership():
    payload = _json_object()
    if payload is None:
        return jsonify({"error": "membership must be a JSON object"}), 400
    try:
        result = normalize_membership(payload, granted_at=payload.get("granted_at"))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(result)


@v44_bp.post("/access/authorize")
def authorize_enterprise_access():
    payload = _json_object()
    if payload is None:
        return jsonify({"error": "access request must be a JSON object"}), 400
    try:
        result = authorize_access(payload)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(result)


@v44_bp.post("/directory/organizations")
def persist_enterprise_organization():
    payload = _json_object()
    actor = _session_subject()
    if payload is None:
        return jsonify({"error": "organization must be a JSON object"}), 400
    if actor is None or getattr(g, "enterprise_api_key", None) is not None:
        return (
            jsonify(
                {
                    "error": "an authenticated user session is required",
                    "code": "USER_SESSION_REQUIRED",
                }
            ),
            403,
        )
    try:
        result = create_organization(payload, actor=actor)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 409
    return jsonify(result), 201 if result["accepted"] else 200


@v44_bp.post("/directory/organizations/<organization_id>/memberships")
def persist_enterprise_membership(organization_id: str):
    payload = _json_object()
    if payload is None:
        return jsonify({"error": "membership must be a JSON object"}), 400
    context, denied = _authorized(organization_id, "membership.manage")
    if denied is not None:
        return denied
    if context is None:
        return jsonify({"error": "enterprise tenant context required"}), 403
    try:
        result = register_membership(
            {**payload, "organization_id": organization_id},
            actor=context["subject"],
        )
    except KeyError as exc:
        return jsonify({"error": str(exc)}), 404
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 409
    return jsonify(result), 201 if result["accepted"] else 200


@v44_bp.put("/session/tenant")
def select_enterprise_tenant():
    payload = _json_object()
    actor = _session_subject()
    if payload is None:
        return jsonify({"error": "tenant selection must be a JSON object"}), 400
    if actor is None or getattr(g, "enterprise_api_key", None) is not None:
        return (
            jsonify(
                {
                    "error": "an authenticated user session is required",
                    "code": "USER_SESSION_REQUIRED",
                }
            ),
            403,
        )
    try:
        context = bind_tenant(
            str(payload.get("organization_id") or ""),
            actor,
        )
    except KeyError as exc:
        return jsonify({"error": str(exc)}), 404
    except (PermissionError, ValueError) as exc:
        return jsonify({"error": str(exc)}), 403
    session["enterprise_tenant"] = context
    return jsonify({"selected": True, "tenant": context})


@v44_bp.get("/session/tenant")
def get_enterprise_tenant():
    context = _request_context()
    if context is None:
        return jsonify({"selected": False, "tenant": None})
    return jsonify({"selected": True, "tenant": context})


@v44_bp.delete("/session/tenant")
def clear_enterprise_tenant():
    if getattr(g, "enterprise_api_key", None) is not None:
        return (
            jsonify(
                {
                    "error": "API key context cannot clear a user session",
                    "code": "USER_SESSION_REQUIRED",
                }
            ),
            403,
        )
    removed = session.pop("enterprise_tenant", None) is not None
    return jsonify({"cleared": removed})


@v44_bp.post("/directory/organizations/<organization_id>/api-keys")
def create_enterprise_api_key(organization_id: str):
    payload = _json_object()
    if payload is None:
        return jsonify({"error": "API key request must be a JSON object"}), 400
    context, denied = _authorized(organization_id, "api-key.manage")
    if denied is not None:
        return denied
    if context is None:
        return jsonify({"error": "enterprise tenant context required"}), 403
    try:
        result = issue_api_key(
            organization_id,
            subject=payload.get("subject"),
            name=payload.get("name"),
            scopes=payload.get("scopes"),
            expires_in_seconds=payload.get("expires_in_seconds"),
            actor=context["subject"],
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(result), 201


@v44_bp.get("/directory/organizations/<organization_id>/api-keys")
def get_enterprise_api_keys(organization_id: str):
    _, denied = _authorized(organization_id, "api-key.manage")
    if denied is not None:
        return denied
    return jsonify(
        {
            "version": "4.4.1",
            "organization_id": organization_id,
            "api_keys": list_api_keys(organization_id),
        }
    )


@v44_bp.post("/directory/organizations/<organization_id>/api-keys/<api_key_id>/rotate")
def rotate_enterprise_api_key(
    organization_id: str,
    api_key_id: str,
):
    payload = _json_object()
    if payload is None:
        return jsonify({"error": "rotation request must be a JSON object"}), 400
    context, denied = _authorized(organization_id, "api-key.manage")
    if denied is not None:
        return denied
    if context is None:
        return jsonify({"error": "enterprise tenant context required"}), 403
    try:
        result = rotate_api_key(
            organization_id,
            api_key_id,
            actor=context["subject"],
            expires_in_seconds=payload.get("expires_in_seconds"),
        )
    except KeyError as exc:
        return jsonify({"error": str(exc)}), 404
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(result)


@v44_bp.post("/directory/organizations/<organization_id>/api-keys/<api_key_id>/revoke")
def revoke_enterprise_api_key(
    organization_id: str,
    api_key_id: str,
):
    _, denied = _authorized(organization_id, "api-key.manage")
    if denied is not None:
        return denied
    try:
        result = revoke_api_key(organization_id, api_key_id)
    except KeyError as exc:
        return jsonify({"error": str(exc)}), 404
    return jsonify(result)


@v44_bp.get("/directory/organizations/<organization_id>/quotas")
def get_enterprise_quotas(organization_id: str):
    _, denied = _authorized(organization_id, "quota.read")
    if denied is not None:
        return denied
    try:
        backend = get_quota_backend()
        policy = backend.get_policy(organization_id)
    except (ValueError, RuntimeError) as exc:
        if isinstance(exc, RuntimeError):
            return _quota_unavailable(exc)
        return jsonify({"error": str(exc)}), 400
    return jsonify(
        {
            "version": "4.4.2",
            "backend": backend.backend,
            "distributed": backend.distributed,
            "policy": policy,
        }
    )


@v44_bp.put("/directory/organizations/<organization_id>/quotas")
def update_enterprise_quotas(organization_id: str):
    payload = _json_object()
    if payload is None:
        return jsonify({"error": "quota policy must be a JSON object"}), 400
    context, denied = _authorized(organization_id, "quota.manage")
    if denied is not None:
        return denied
    if context is None:
        return jsonify({"error": "enterprise tenant context required"}), 403
    try:
        policy = normalize_quota_policy(
            organization_id,
            payload,
            updated_by=context["subject"],
            updated_at=time.time(),
        )
        backend = get_quota_backend()
        stored = backend.set_policy(policy)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except RuntimeError as exc:
        return _quota_unavailable(exc)
    return jsonify(
        {
            "version": "4.4.2",
            "updated": True,
            "backend": backend.backend,
            "policy": stored,
        }
    )


@v44_bp.get("/directory/organizations/<organization_id>/usage")
def get_enterprise_usage(organization_id: str):
    _, denied = _authorized(organization_id, "quota.read")
    if denied is not None:
        return denied
    api_key_id = request.args.get("api_key_id")
    if api_key_id is not None:
        record = EnterpriseApiKey.query.filter_by(
            api_key_id=api_key_id,
            organization_id=organization_id,
        ).one_or_none()
        if record is None:
            return jsonify({"error": "API key not found"}), 404
    try:
        backend = get_quota_backend()
        snapshot = backend.usage(
            organization_id,
            api_key_id=api_key_id,
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except RuntimeError as exc:
        return _quota_unavailable(exc)
    snapshot["backend"] = backend.backend
    snapshot["distributed"] = backend.distributed
    return jsonify(snapshot)


@v44_bp.post("/public/decisions/ensemble")
def public_decision_ensemble():
    payload = _json_object()
    if payload is None:
        return jsonify({"error": "decision request must be a JSON object"}), 400
    try:
        models = _bounded_object_list(
            payload.get("models"),
            "models",
            MAX_PUBLIC_MODELS,
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    quota, denied = _meter_public_decision("decision.ensemble", payload)
    if denied is not None:
        return denied
    if quota is None:
        return jsonify({"error": "quota decision unavailable"}), 503
    return _public_response(
        "decision.ensemble",
        ensemble_decision(models),
        quota,
    )


@v44_bp.post("/public/decisions/scenario")
def public_decision_scenario():
    payload = _json_object()
    if payload is None or not isinstance(payload.get("baseline"), dict):
        return jsonify({"error": "baseline must be a JSON object"}), 400
    try:
        _finite_number(
            payload["baseline"].get("probability", 0.5),
            "baseline probability",
        )
        scenarios = _bounded_object_list(
            payload.get("scenarios", []),
            "scenarios",
            MAX_PUBLIC_SCENARIOS,
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    quota, denied = _meter_public_decision("decision.scenario", payload)
    if denied is not None:
        return denied
    if quota is None:
        return jsonify({"error": "quota decision unavailable"}), 503
    return _public_response(
        "decision.scenario",
        scenario_decision(payload["baseline"], scenarios),
        quota,
    )


@v44_bp.post("/public/decisions/brief")
def public_decision_brief():
    payload = _json_object()
    if payload is None or not isinstance(payload.get("ensemble"), dict):
        return jsonify({"error": "ensemble must be a JSON object"}), 400
    scenario = payload.get("scenario")
    if scenario is not None and not isinstance(scenario, dict):
        return jsonify({"error": "scenario must be a JSON object"}), 400
    try:
        _finite_number(
            (scenario or payload["ensemble"]).get(
                "adjusted_probability",
                payload["ensemble"].get("probability", 0.5),
            ),
            "decision probability",
        )
        _finite_number(payload["ensemble"].get("confidence", 0), "ensemble confidence")
        _finite_number(payload["ensemble"].get("disagreement", 0), "ensemble disagreement")
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    quota, denied = _meter_public_decision("decision.brief", payload)
    if denied is not None:
        return denied
    if quota is None:
        return jsonify({"error": "quota decision unavailable"}), 503
    return _public_response(
        "decision.brief",
        decision_brief(payload["ensemble"], scenario),
        quota,
    )
