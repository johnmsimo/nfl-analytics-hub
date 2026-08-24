#!/usr/bin/env python3
"""Run one credit-capped The Odds API production smoke request.

This utility deliberately bypasses the app's ENABLE_ODDS_API runtime gate so a
credential can be validated before normal traffic is enabled. It never retries,
never follows redirects, and never prints the credential or request URL.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections.abc import Callable
from typing import Any

import requests

API_BASE = "https://api.the-odds-api.com/v4"
SPORT = "americanfootball_nfl"
PROVIDER_KEY = "the-odds-api"
REGION = "us"
MARKET = "h2h"
_EVENT_ID = re.compile(r"^[0-9a-f]{32}$")


class SmokeError(RuntimeError):
    """A sanitized failure safe to print in CI logs."""


def _validate_preflight(event_id: str, confirm_credit_cost: int) -> str:
    if confirm_credit_cost != 1:
        raise SmokeError("confirmation_required: pass --confirm-credit-cost 1")
    if not _EVENT_ID.fullmatch(event_id):
        raise SmokeError("invalid_event_id: expected 32 lowercase hexadecimal characters")

    providers = {
        item.strip().lower()
        for item in os.environ.get("ENABLED_PROVIDERS", "").split(",")
        if item.strip()
    }
    if PROVIDER_KEY not in providers:
        raise SmokeError(
            "provider_disabled: ENABLED_PROVIDERS must contain the-odds-api"
        )

    key = os.environ.get("ODDS_API_KEY")
    if not key:
        raise SmokeError("key_missing: ODDS_API_KEY is not configured")
    return key


def _header_int(response: Any, name: str) -> int:
    value = response.headers.get(name)
    if value is None:
        raise SmokeError(f"missing_usage_header: {name}")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise SmokeError(f"invalid_usage_header: {name}") from exc


def run_smoke(
    event_id: str,
    confirm_credit_cost: int,
    *,
    get: Callable[..., Any] = requests.get,
) -> dict[str, Any]:
    """Make exactly one event-odds request, capped at one reported credit."""
    key = _validate_preflight(event_id, confirm_credit_cost)
    url = f"{API_BASE}/sports/{SPORT}/events/{event_id}/odds"

    try:
        response = get(
            url,
            params={
                "apiKey": key,
                "regions": REGION,
                "markets": MARKET,
                "oddsFormat": "american",
            },
            timeout=30,
            allow_redirects=False,
        )
    except requests.RequestException as exc:
        raise SmokeError("provider_request_failed") from exc

    if response.status_code != 200:
        raise SmokeError(f"provider_http_status: {response.status_code}")

    try:
        payload = response.json()
    except (TypeError, ValueError) as exc:
        raise SmokeError("invalid_json_response") from exc
    if not isinstance(payload, dict):
        raise SmokeError("invalid_event_response")
    if payload.get("id") != event_id or payload.get("sport_key") != SPORT:
        raise SmokeError("unexpected_event_response")

    bookmakers = payload.get("bookmakers")
    if not isinstance(bookmakers, list) or not bookmakers:
        raise SmokeError("no_bookmakers_returned")
    has_market = any(
        market.get("key") == MARKET
        for book in bookmakers
        if isinstance(book, dict)
        for market in book.get("markets", [])
        if isinstance(market, dict)
    )
    if not has_market:
        raise SmokeError("h2h_market_missing")

    requests_last = _header_int(response, "x-requests-last")
    requests_used = _header_int(response, "x-requests-used")
    requests_remaining = _header_int(response, "x-requests-remaining")
    if requests_last < 0 or requests_last > 1:
        raise SmokeError(f"credit_cap_exceeded: provider_reported={requests_last}")

    return {
        "ok": True,
        "provider": PROVIDER_KEY,
        "sport": SPORT,
        "event_id": event_id,
        "region": REGION,
        "market": MARKET,
        "bookmakers": len(bookmakers),
        "requests_last": requests_last,
        "requests_used": requests_used,
        "requests_remaining": requests_remaining,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--event-id", required=True)
    parser.add_argument("--confirm-credit-cost", required=True, type=int)
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        result = run_smoke(args.event_id, args.confirm_credit_cost)
    except SmokeError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}), file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
