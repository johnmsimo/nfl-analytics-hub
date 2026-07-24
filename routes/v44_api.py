"""NFL Analytics Hub v4.4 enterprise access endpoints."""

from __future__ import annotations

from typing import Any

from flask import Blueprint, g, jsonify, request, session

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
from enterprise_v44 import (
    authorize_access,
    enterprise_manifest,
    normalize_membership,
    normalize_organization,
    role_catalog,
)

v44_bp = Blueprint("v44_api", __name__, url_prefix="/api/v4.4")


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
            },
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
