"""
The Odds API layer for NFL (americanfootball_nfl).

Ported pattern from the MLB hub: one persisted snapshot survives redeploys so
normal product traffic does not repeatedly spend provider credits. P3.6 adds
cache-only reads plus quote provenance/timestamps so stale prices can be shown
for context but never promoted into actionable bets.

Degrades gracefully: the API key, canonical provider registration, and explicit
runtime feature gate must all be present before any provider request is made.
"""
from __future__ import annotations

import json
import os
import threading
import time
from datetime import UTC, datetime

import http_client
import nfl_data

API_BASE = "https://api.the-odds-api.com/v4"
SPORT = "americanfootball_nfl"
PROVIDER_KEY = "the-odds-api"
_TRUTHY = {"1", "true", "yes", "on"}

GAME_MARKETS = "h2h,spreads,totals"
PROP_MARKETS = [
    "player_pass_yds",
    "player_pass_tds",
    "player_rush_yds",
    "player_receptions",
    "player_reception_yds",
    "player_anytime_td",
]
_ALT_MARKETS = [
    "player_pass_yds_alternate",
    "player_rush_yds_alternate",
    "player_receptions_alternate",
    "player_reception_yds_alternate",
]

_REGION = os.environ.get("ODDS_REGION", "us")
_GAME_TTL = int(os.environ.get("NFL_ODDS_GAME_TTL_SEC", "21600"))
_PROPS_TTL = int(os.environ.get("NFL_ODDS_PROPS_TTL_SEC", "21600"))
_INCLUDE_ALT = os.environ.get("NFL_ODDS_INCLUDE_ALT", "0") == "1"

_CACHE_FILE = os.path.join(nfl_data.DATA_DIR, "odds_cache.json")
_lock = threading.RLock()
_snapshot: dict | None = None


def _api_key() -> str | None:
    return os.environ.get("ODDS_API_KEY") or None


def has_api_key() -> bool:
    """Return key presence without exposing the credential."""
    return _api_key() is not None


def provider_enabled() -> bool:
    """Require the canonical integration registry key, never the old odds alias."""
    providers = {
        item.strip().lower()
        for item in os.environ.get("ENABLED_PROVIDERS", "").split(",")
        if item.strip()
    }
    return PROVIDER_KEY in providers


def feature_enabled() -> bool:
    """Explicit credit-spend gate shared by every Odds API runtime path."""
    return os.environ.get("ENABLE_ODDS_API", "false").strip().lower() in _TRUTHY


def is_configured() -> bool:
    return has_api_key() and provider_enabled() and feature_enabled()


def _today() -> str:
    return datetime.now(UTC).date().isoformat()


def _load_snapshot() -> dict:
    global _snapshot
    with _lock:
        if _snapshot is not None:
            return _snapshot
        try:
            with open(_CACHE_FILE, "r", encoding="utf-8") as file:
                _snapshot = json.load(file)
        except Exception:  # noqa: BLE001
            _snapshot = {}
        if not isinstance(_snapshot, dict):
            _snapshot = {}
        return _snapshot


def _save_snapshot() -> None:
    with _lock:
        tmp = _CACHE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as file:
            json.dump(_snapshot, file, separators=(",", ":"))
        os.replace(tmp, _CACHE_FILE)


def _get(path: str, **params):
    if not is_configured():
        return None
    key = _api_key()
    if not key:  # Defensive: is_configured() already requires this.
        return None
    params = {"apiKey": key, **params}
    response = http_client.get(f"{API_BASE}{path}", params=params)
    if response.status_code != 200:
        raise RuntimeError(f"Odds API {response.status_code}: {response.text[:200]}")
    return response.json()


def _age_seconds(value) -> float | None:
    try:
        return round(max(0.0, time.time() - float(value)), 1)
    except (TypeError, ValueError):
        return None


# ----------------------------------------------------------------- game odds

def peek_game_odds() -> list[dict]:
    """Return cached game events without making a provider request."""
    snap = _load_snapshot()
    with _lock:
        block = snap.get("game_odds") or {}
        events = block.get("events") or []
        return list(events) if isinstance(events, list) else []


def get_game_odds(force: bool = False) -> list[dict]:
    """Featured markets (h2h/spreads/totals) for all upcoming NFL events.

    The persisted snapshot is reused within the configured TTL. A provider call
    occurs only when the integration is fully enabled and the snapshot is stale
    or ``force=True``.
    """
    if not is_configured():
        return []
    snap = _load_snapshot()
    with _lock:
        block = snap.get("game_odds") or {}
        fresh = (
            block.get("date") == _today()
            and time.time() - block.get("fetched_at", 0) < _GAME_TTL
        )
        if fresh and not force:
            return block.get("events", [])
    events = (
        _get(
            f"/sports/{SPORT}/odds",
            regions=_REGION,
            markets=GAME_MARKETS,
            oddsFormat="american",
        )
        or []
    )
    with _lock:
        snap["game_odds"] = {
            "date": _today(),
            "fetched_at": time.time(),
            "events": events,
        }
        _save_snapshot()
    return events


def _norm(name: str | None) -> str:
    return (name or "").lower().strip()


def norm_player_name(name: str | None) -> str:
    """Normalize a player name for cross-source matching (ESPN <-> books)."""
    import unicodedata

    text = unicodedata.normalize("NFKD", (name or "")).encode("ascii", "ignore").decode()
    return text.lower().replace(".", "").replace("'", "").replace("-", " ").strip()


def _nickname(name: str | None) -> str:
    """Last word of a club name. Unique across all 32 NFL teams."""
    parts = _norm(name).split()
    return parts[-1] if parts else ""


def _match_event(game: dict, events: list[dict]) -> dict | None:
    home, away = _norm(game.get("home_name")), _norm(game.get("away_name"))
    for event in events:
        if _norm(event.get("home_team")) == home and _norm(event.get("away_team")) == away:
            return event
    home_nick = _nickname(game.get("home_name"))
    away_nick = _nickname(game.get("away_name"))
    if not home_nick or not away_nick:
        return None
    for event in events:
        if (
            _nickname(event.get("home_team")) == home_nick
            and _nickname(event.get("away_team")) == away_nick
        ):
            return event
    return None


def find_event_for_game(game: dict, *, cache_only: bool = False, force: bool = False) -> dict | None:
    """Match an ESPN game to an Odds API event.

    ``cache_only=True`` is the zero-credit P3.6 verification/product fallback.
    The default keeps the historical zero-argument ``get_game_odds()`` call
    contract; only an explicit forced refresh passes the new keyword argument.
    """
    if cache_only:
        events = peek_game_odds()
    elif force:
        events = get_game_odds(force=True)
    else:
        events = get_game_odds()
    return _match_event(game, events)


# ---------------------------------------------------------------- event props

def _prop_market_keys() -> list[str]:
    return PROP_MARKETS + (_ALT_MARKETS if _INCLUDE_ALT else [])


def event_props_snapshot(odds_event_id: str) -> dict:
    """Return cached event-prop data and provenance without provider access."""
    snap = _load_snapshot()
    with _lock:
        cell = dict((snap.get("event_props") or {}).get(odds_event_id) or {})
    fetched_at = cell.get("fetched_at")
    return {
        "event_id": odds_event_id,
        "date": cell.get("date"),
        "fetched_at": fetched_at,
        "age_seconds": _age_seconds(fetched_at),
        "closing": bool(cell.get("closing")),
        "data": cell.get("data"),
        "available": "data" in cell and cell.get("data") is not None,
    }


def peek_event_props(odds_event_id: str) -> dict | None:
    """Return cached props for an event without making a provider request."""
    return event_props_snapshot(odds_event_id).get("data")


def get_event_props(odds_event_id: str, force: bool = False) -> dict | None:
    """Player-prop odds for one event, snapshot-cached per day."""
    if not is_configured():
        return None
    snap = _load_snapshot()
    with _lock:
        cell = (snap.get("event_props") or {}).get(odds_event_id)
        if (
            cell
            and not force
            and cell.get("date") == _today()
            and time.time() - cell.get("fetched_at", 0) < _PROPS_TTL
        ):
            return cell.get("data")
    try:
        data = _get(
            f"/sports/{SPORT}/events/{odds_event_id}/odds",
            regions=_REGION,
            markets=",".join(_prop_market_keys()),
            oddsFormat="american",
        )
    except RuntimeError as exc:
        # 422 = markets not yet posted for this event; cache the miss briefly.
        data = None
        if "422" not in str(exc):
            raise
    with _lock:
        snap.setdefault("event_props", {})[odds_event_id] = {
            "date": _today(),
            "fetched_at": time.time(),
            "data": data,
        }
        _save_snapshot()
    return data


def refresh_game_props(game: dict, *, force_game_events: bool = False) -> dict:
    """Explicitly hydrate one game's prop snapshot.

    This helper can spend provider credits and is intended for protected/manual
    workflows or tightly targeted product refreshes, never broad verification.
    """
    event = find_event_for_game(game, force=force_game_events)
    if not event:
        return {"ok": False, "reason": "event_not_found", "event_id": None}
    data = get_event_props(str(event["id"]), force=True)
    snapshot = event_props_snapshot(str(event["id"]))
    return {
        "ok": data is not None,
        "reason": None if data is not None else "props_unavailable",
        "event_id": event["id"],
        "fetched_at": snapshot.get("fetched_at"),
        "age_seconds": snapshot.get("age_seconds"),
    }


def fetch_event_odds_live(odds_event_id: str, markets: list[str] | None = None) -> dict | None:
    """Force-refresh one event right now, bypassing the normal snapshot TTL."""
    if not is_configured():
        return None
    data = _get(
        f"/sports/{SPORT}/events/{odds_event_id}/odds",
        regions=_REGION,
        markets=",".join(markets or (_prop_market_keys() + GAME_MARKETS.split(","))),
        oddsFormat="american",
    )
    with _lock:
        snap = _load_snapshot()
        snap.setdefault("event_props", {})[odds_event_id] = {
            "date": _today(),
            "fetched_at": time.time(),
            "data": data,
            "closing": True,
        }
        _save_snapshot()
    return data


# ------------------------------------------------------------------- parsing

def parse_game_markets(event: dict) -> dict:
    """One Odds API event -> normalized h2h/spread/total rows."""
    out: dict = {"h2h": [], "spreads": [], "totals": []}
    home, away = event.get("home_team"), event.get("away_team")
    for bookmaker in event.get("bookmakers", []):
        book = bookmaker.get("title") or bookmaker.get("key")
        for market in bookmaker.get("markets", []):
            market_key = market.get("key")
            outcomes = {outcome.get("name"): outcome for outcome in market.get("outcomes", [])}
            if market_key == "h2h" and home in outcomes and away in outcomes:
                out["h2h"].append(
                    {
                        "book": book,
                        "home_price": outcomes[home].get("price"),
                        "away_price": outcomes[away].get("price"),
                    }
                )
            elif market_key == "spreads" and home in outcomes and away in outcomes:
                out["spreads"].append(
                    {
                        "book": book,
                        "home_point": outcomes[home].get("point"),
                        "home_price": outcomes[home].get("price"),
                        "away_point": outcomes[away].get("point"),
                        "away_price": outcomes[away].get("price"),
                    }
                )
            elif market_key == "totals":
                over, under = outcomes.get("Over"), outcomes.get("Under")
                if over and under:
                    out["totals"].append(
                        {
                            "book": book,
                            "point": over.get("point"),
                            "over_price": over.get("price"),
                            "under_price": under.get("price"),
                        }
                    )
    return out


def parse_prop_markets(event_odds: dict | None, *, fetched_at=None) -> list[dict]:
    """Event props payload -> flat timestamped quote rows.

    P3.6 preserves provider update time plus local snapshot fetch time. Existing
    callers may continue supplying only ``event_odds``.
    """
    rows: list[dict] = []
    if not event_odds:
        return rows
    for bookmaker in event_odds.get("bookmakers", []):
        book = bookmaker.get("title") or bookmaker.get("key")
        book_key = bookmaker.get("key") or book
        book_last_update = bookmaker.get("last_update")
        for market in bookmaker.get("markets", []):
            market_key = market.get("key") or ""
            base = market_key.replace("_alternate", "")
            is_alt = market_key.endswith("_alternate")
            market_last_update = market.get("last_update")
            for outcome in market.get("outcomes", []):
                player = outcome.get("description") or outcome.get("name")
                side = _norm(outcome.get("name"))
                common = {
                    "market_key": market_key,
                    "base_key": base,
                    "is_alt": is_alt,
                    "player": player,
                    "price": outcome.get("price"),
                    "book": book,
                    "book_key": book_key,
                    "book_last_update": book_last_update,
                    "market_last_update": market_last_update,
                    "fetched_at": fetched_at,
                }
                if base == "player_anytime_td":
                    if side not in ("yes", "no"):
                        continue
                    rows.append(
                        {
                            **common,
                            "line": 0.5,
                            "side": "over" if side == "yes" else "under",
                        }
                    )
                elif side in ("over", "under"):
                    rows.append(
                        {
                            **common,
                            "line": outcome.get("point"),
                            "side": side,
                        }
                    )
    return rows


def snapshot_status() -> dict:
    snap = _load_snapshot()
    block = snap.get("game_odds") or {}
    event_cells = snap.get("event_props") or {}
    event_ages = [
        age
        for age in (_age_seconds(cell.get("fetched_at")) for cell in event_cells.values())
        if age is not None
    ]
    return {
        "provider_key": PROVIDER_KEY,
        "key_configured": has_api_key(),
        "provider_enabled": provider_enabled(),
        "feature_enabled": feature_enabled(),
        "configured": is_configured(),
        "snapshot_date": block.get("date"),
        "game_events": len(block.get("events", [])),
        "game_snapshot_age_seconds": _age_seconds(block.get("fetched_at")),
        "event_props_cached": len(event_cells),
        "freshest_event_props_age_seconds": min(event_ages) if event_ages else None,
        "oldest_event_props_age_seconds": max(event_ages) if event_ages else None,
    }
