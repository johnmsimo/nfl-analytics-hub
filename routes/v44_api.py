"""NFL Analytics Hub v4.4 enterprise access endpoints."""

from __future__ import annotations

from typing import Any

from flask import Blueprint, jsonify, request

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
            },
        }
    )


@v44_bp.get("/access/roles")
def access_roles():
    return jsonify({"version": "4.4.0", "roles": role_catalog()})


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
