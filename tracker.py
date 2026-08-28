"""
Bet tracker — persistent pick CRUD, grading, closing-line value (CLV).

P3.7 keeps the long-standing Tracker API/UI contract while moving its canonical
state from Fly-local JSON files into PostgreSQL. The legacy files remain a
best-effort development mirror/bootstrap source only.

Primary KPI remains CLV: clvEdge = closingImplied - openingImplied (positive =
you beat the close). P3.7 also reports Brier/ECE for graded tracked picks and
preserves an immutable release fingerprint for each first-saved selection.

Grading reads final stats from nfl_data's boxscore-fed weekly rows (ESPN,
available minutes after games end) and final scores from the schedule.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
import uuid
from datetime import datetime, timezone, timedelta
from typing import Any

import nfl_data
import odds_api
import projections
import tracker_store
import value_engine as ve

_norm_name = odds_api.norm_player_name

_STORE_FILE = os.path.join(nfl_data.DATA_DIR, "daily_tracker.json")
_SETTINGS_FILE = os.path.join(nfl_data.DATA_DIR, "model_adjustments.json")
_lock = threading.RLock()
_store_cache: tuple | None = None      # file-fallback cache only
_closing_captured: set[str] = set()    # game_ids already captured this process

PROP_STAT = {
    "pass_yds": "passing_yards", "pass_tds": "passing_tds",
    "rush_yds": "rushing_yards", "receptions": "receptions",
    "rec_yds": "receiving_yards",
}
GAME_MARKETS = ("h2h", "spread", "total")

CLOSING_ENABLED = os.environ.get("TRACKER_CLOSING_CAPTURE_ENABLED", "1") == "1"
CLOSING_INTERVAL_MIN = int(os.environ.get("TRACKER_CLOSING_CAPTURE_MINUTES", "5"))
CLOSING_LEAD_MIN = int(os.environ.get("TRACKER_CLOSING_LEAD_MIN", "20"))
CLOSING_GRACE_MIN = int(os.environ.get("TRACKER_CLOSING_GRACE_MIN", "15"))
AUTO_SYNC_MIN = int(os.environ.get("TRACKER_AUTO_SYNC_MINUTES", "30"))


# ---------------------------------------------------------------------- store

def _sig() -> tuple:
    try:
        st = os.stat(_STORE_FILE)
        return (st.st_mtime_ns, st.st_size)
    except OSError:
        return (0, 0)


def _read_json_file(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        return payload if isinstance(payload, dict) else {}
    except Exception:  # noqa: BLE001 - legacy/fallback files are best effort
        return {}


def _write_json_file(path: str, payload: dict) -> None:
    try:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, separators=(",", ":"))
        os.replace(tmp, path)
    except Exception:  # noqa: BLE001 - PostgreSQL remains canonical in production
        pass


def _load() -> dict:
    """Load canonical Tracker state, bootstrapping a legacy file when needed."""
    global _store_cache
    with _lock:
        persistence = tracker_store.persistence_status()
        if persistence.get("available"):
            store = tracker_store.load_store()
            if store:
                return json.loads(json.dumps(store))
            legacy = _read_json_file(_STORE_FILE)
            if legacy:
                tracker_store.save_store(legacy)
                return json.loads(json.dumps(legacy))
            return {}

        sig = _sig()
        if _store_cache and _store_cache[0] == sig:
            return json.loads(json.dumps(_store_cache[1]))
        store = _read_json_file(_STORE_FILE)
        _store_cache = (sig, store)
        return json.loads(json.dumps(store))


def _save(store: dict) -> None:
    global _store_cache
    with _lock:
        persisted = tracker_store.save_store(store)
        # Keep a local mirror for development/recovery, but production truth is
        # the database whenever persistence is available.
        _write_json_file(_STORE_FILE, store)
        _store_cache = (_sig(), store)
        if not persisted:
            return


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def get_settings() -> dict:
    defaults = {"bankroll": 1000.0, "kelly_fraction": 0.25,
                "max_bet_pct": 0.05, "unit_pct": 0.01}
    persistence = tracker_store.persistence_status()
    if persistence.get("available"):
        stored = tracker_store.load_settings()
        if stored:
            defaults.update(stored)
            return defaults
        legacy = _read_json_file(_SETTINGS_FILE)
        defaults.update(legacy)
        tracker_store.save_settings(defaults)
        return defaults
    defaults.update(_read_json_file(_SETTINGS_FILE))
    return defaults


def save_settings(patch: dict) -> dict:
    cur = get_settings()
    for key in ("bankroll", "kelly_fraction", "max_bet_pct", "unit_pct"):
        if key in patch:
            try:
                cur[key] = float(patch[key])
            except (TypeError, ValueError):
                pass
    tracker_store.save_settings(cur)
    _write_json_file(_SETTINGS_FILE, cur)
    return cur


def persistence_status() -> dict[str, Any]:
    status = tracker_store.persistence_status()
    return {
        **status,
        "legacyMirrorPresent": os.path.exists(_STORE_FILE),
        "settingsMirrorPresent": os.path.exists(_SETTINGS_FILE),
    }


# ---------------------------------------------------------------------- picks

_PICK_FIELDS = (
    "gameId", "season", "week", "gameday", "player", "playerId",
    "team", "opponent", "position", "marketKey", "marketLabel",
    "line", "side", "price", "book", "stakeDollars", "stakeUnits",
    "modelProb", "impliedProb", "fairProb", "fairMarketProb", "referenceProb",
    "edge", "evPct", "kellyPct", "modelSource", "decisionModelVersion", "source",
    "modelMean", "consensusProb", "simulationProb", "simulationAgreement",
    "confidenceScore", "confidenceGrade", "matchupGrade", "decisionGrade",
    "decisionScore", "decisionReasons", "decisionRisks", "priceStatus",
    "quoteStatus", "bestPrice", "freshBookCount", "pairedFairBookCount",
    "marketPricing", "oddsSnapshotAgeSeconds", "actionable", "evidenceSeason",
    "rosterVerified",
)


def _release_fingerprint(entry: dict) -> str:
    release = {key: entry.get(key) for key in _PICK_FIELDS if key in entry}
    raw = json.dumps(release, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def add_pick(payload: dict) -> dict:
    date = payload.get("gameday") or _today()
    entry = {key: payload.get(key) for key in _PICK_FIELDS}
    entry["id"] = payload.get("id") or uuid.uuid4().hex[:12]
    entry["savedAt"] = _now()
    entry["grade"] = "pending"
    entry["gradedAt"] = None
    if entry.get("price") is None and isinstance(entry.get("bestPrice"), dict):
        best = entry["bestPrice"]
        if isinstance(best.get("price"), (int, float)):
            entry["price"] = best.get("price")
        if not entry.get("book") and best.get("book"):
            entry["book"] = best.get("book")
    if entry.get("price") is not None:
        entry["openingImplied"] = ve.american_to_implied(entry["price"])
    entry["releaseFingerprint"] = _release_fingerprint(entry)
    entry["receiptVersion"] = "p3.7"

    store = _load()
    day = store.setdefault(date, {"entries": []})
    dedup = (date, entry.get("gameId"), entry.get("player"),
             entry.get("marketKey"), entry.get("line"), entry.get("side"))
    for existing in day["entries"]:
        existing_key = (
            date,
            existing.get("gameId"),
            existing.get("player"),
            existing.get("marketKey"),
            existing.get("line"),
            existing.get("side"),
        )
        if existing_key == dedup:
            # First publication is immutable. A duplicate save may update only
            # user allocation fields; model/price/result history is preserved.
            changed = False
            for key in ("stakeDollars", "stakeUnits"):
                if payload.get(key) is not None and existing.get(key) != payload.get(key):
                    existing[key] = payload.get(key)
                    changed = True
            if changed:
                _save(store)
            return existing
    day["entries"].append(entry)
    _save(store)
    return entry


def update_pick(date: str, pick_id: str, patch: dict) -> dict | None:
    store = _load()
    for entry in store.get(date, {}).get("entries", []):
        if entry["id"] == pick_id:
            for key, value in patch.items():
                if key not in ("id", "savedAt", "releaseFingerprint", "receiptVersion"):
                    entry[key] = value
            _save(store)
            return entry
    return None


def delete_pick(date: str, pick_id: str) -> bool:
    store = _load()
    day = store.get(date)
    if not day:
        return False
    before = len(day["entries"])
    day["entries"] = [entry for entry in day["entries"] if entry["id"] != pick_id]
    if len(day["entries"]) == before:
        return False
    _save(store)
    return True


def list_picks(date: str | None = None) -> dict:
    store = _load()
    if date:
        return {date: store.get(date, {"entries": []})}
    return store


# -------------------------------------------------------------------- grading

def _grade_game_market(entry: dict, game: dict) -> tuple[str, float] | None:
    home_score, away_score = game.get("home_score"), game.get("away_score")
    if home_score is None or away_score is None:
        return None
    market, side, line = entry["marketKey"], entry.get("side"), entry.get("line")
    if market == "h2h":
        if home_score == away_score:
            return ("push", 0.0)
        winner = "home" if home_score > away_score else "away"
        return ("win" if side == winner else "loss", float(home_score if side == "home" else away_score))
    if market == "spread":
        margin = (home_score - away_score) if side == "home" else (away_score - home_score)
        adjusted = margin + float(line or 0)
        if adjusted == 0:
            return ("push", margin)
        return ("win" if adjusted > 0 else "loss", margin)
    if market == "total":
        total = home_score + away_score
        if total == line:
            return ("push", total)
        over = total > float(line or 0)
        return ("win" if (side == "over") == over else "loss", total)
    return None


def _grade_prop(entry: dict, stat_rows: dict) -> tuple[str, float] | None:
    row = stat_rows.get((entry.get("gameId"), str(entry.get("playerId"))))
    if row is None:
        return None
    market = entry["marketKey"]
    if market == "anytime_td":
        actual = row["rushing_tds"] + row["receiving_tds"]
    else:
        column = PROP_STAT.get(market)
        if not column:
            return None
        actual = row[column]
    line = float(entry.get("line") or 0)
    if actual == line:
        return ("push", actual)
    over = actual > line
    return ("win" if (entry.get("side") == "over") == over else "loss", actual)


def grade_pending() -> dict:
    """Grade every pending pick whose game is final. Returns counts."""
    store = _load()
    seasons: set[int] = set()
    for day in store.values():
        for entry in day.get("entries", []):
            if entry.get("grade") == "pending" and entry.get("season"):
                seasons.add(int(entry["season"]))
    stat_rows: dict = {}
    games_by_id: dict = {}
    for season in seasons:
        for game in nfl_data.get_schedule(season):
            games_by_id[game["game_id"]] = game
        for row in nfl_data.get_player_week_stats(season):
            stat_rows[(row["game_id"], row["player_id"])] = row

    graded = 0
    for day in store.values():
        for entry in day.get("entries", []):
            if entry.get("grade") != "pending":
                continue
            game = games_by_id.get(entry.get("gameId"))
            if not game or not game.get("completed"):
                continue
            result = (
                _grade_game_market(entry, game)
                if entry["marketKey"] in GAME_MARKETS
                else _grade_prop(entry, stat_rows)
            )
            if not result:
                continue
            grade, actual = result
            entry["grade"] = grade
            entry["actual"] = actual
            entry["gradedAt"] = _now()
            stake = float(entry.get("stakeDollars") or 0)
            decimal = ve.american_to_decimal(entry.get("price")) or 1.0
            entry["profitDollars"] = round(
                stake * (decimal - 1) if grade == "win"
                else (-stake if grade == "loss" else 0.0), 2)
            graded += 1
        if any(item.get("gradedAt") for item in day.get("entries", [])):
            day["gradedAt"] = _now()
    if graded:
        _save(store)
    return {"graded": graded}


# ------------------------------------------------------------ closing capture

def _kickoff_window(game: dict) -> bool:
    try:
        kickoff = datetime.fromisoformat(game["date"].replace("Z", "+00:00"))
    except Exception:  # noqa: BLE001
        return False
    now = datetime.now(timezone.utc)
    return (
        kickoff - timedelta(minutes=CLOSING_LEAD_MIN)
        <= now
        <= kickoff + timedelta(minutes=CLOSING_GRACE_MIN)
    )


def closing_capture_once() -> dict:
    """Capture one closing quote per pending tracked game around kickoff."""
    if not odds_api.is_configured():
        return {"captured": 0, "reason": "odds not configured"}
    store = _load()
    captured = 0
    changed = False
    for day in store.values():
        pending = [
            entry for entry in day.get("entries", [])
            if entry.get("grade") == "pending" and entry.get("closingPrice") is None
        ]
        if not pending:
            continue
        by_game: dict[str, list[dict]] = {}
        for entry in pending:
            if entry.get("gameId"):
                by_game.setdefault(entry["gameId"], []).append(entry)
        attempted = set(day.get("closingAttempted") or [])
        for game_id, entries in by_game.items():
            season = int(entries[0].get("season") or nfl_data.default_season())
            game = next(
                (candidate for candidate in nfl_data.get_schedule(season) if candidate["game_id"] == game_id),
                None,
            )
            if not game or game_id in _closing_captured or game_id in attempted:
                continue
            if not _kickoff_window(game):
                continue
            event = odds_api.find_event_for_game(game)
            if not event:
                continue
            data = odds_api.fetch_event_odds_live(event["id"])
            _closing_captured.add(game_id)
            attempted.add(game_id)
            day["closingAttempted"] = sorted(attempted)
            changed = True
            prop_rows = odds_api.parse_prop_markets(data or {})
            game_markets = odds_api.parse_game_markets({**event, **(data or {})})
            for entry in entries:
                price = _closing_price_for(entry, prop_rows, game_markets)
                if price is None:
                    continue
                entry["closingPrice"] = price
                closing_imp = ve.american_to_implied(price)
                opening_imp = entry.get("openingImplied") or ve.american_to_implied(entry.get("price"))
                if closing_imp is not None and opening_imp is not None:
                    entry["closingImplied"] = round(closing_imp, 4)
                    entry["clvEdge"] = round(closing_imp - opening_imp, 4)
                captured += 1
                changed = True
        if captured:
            day["closingCapturedAt"] = _now()
    if changed:
        _save(store)
    return {"captured": captured}


def _closing_price_for(entry: dict, prop_rows: list[dict], game_markets: dict):
    """Best closing price for the pick's exact (player, market, line, side)."""
    market, side, line = entry.get("marketKey"), entry.get("side"), entry.get("line")
    if market in GAME_MARKETS:
        block = {
            "h2h": game_markets.get("h2h", []),
            "spread": game_markets.get("spreads", []),
            "total": game_markets.get("totals", []),
        }[market]
        best = None
        for row in block:
            price = row.get(f"{side}_price")
            if market == "spread" and row.get(f"{side}_point") != line:
                continue
            if market == "total" and row.get("point") != line:
                continue
            if isinstance(price, (int, float)):
                decimal = ve.american_to_decimal(price) or 0
                if best is None or decimal > best[0]:
                    best = (decimal, price)
        return best[1] if best else None
    inverse = {value: key for key, value in projections.ODDS_KEY_TO_MARKET.items()}
    wanted = inverse.get(market)
    normalized = _norm_name(entry.get("player"))
    best = None
    for row in prop_rows:
        if (
            row["base_key"] == wanted
            and row["side"] == side
            and row["line"] == line
            and _norm_name(row["player"]) == normalized
            and isinstance(row.get("price"), (int, float))
        ):
            decimal = ve.american_to_decimal(row["price"]) or 0
            if best is None or decimal > best[0]:
                best = (decimal, row["price"])
    return best[1] if best else None


# ----------------------------------------------------------------- live pace

def live_status() -> dict:
    """Live pace for pending picks whose games are in progress."""
    store = _load()
    pending = [
        entry for day in store.values() for entry in day.get("entries", [])
        if entry.get("grade") == "pending" and entry.get("gameId")
    ]
    if not pending:
        return {"live": False, "picks": []}
    seasons = {int(entry.get("season") or nfl_data.default_season()) for entry in pending}
    games: dict[str, dict] = {}
    for season in seasons:
        current = nfl_data.current_week(season)
        for game in nfl_data.fetch_week_scoreboard(
            season,
            current["week"],
            2 if current["season_type"] == "REG" else 3,
        ):
            games[game["game_id"]] = game
    picks = []
    any_live = False
    for entry in pending:
        game = games.get(entry["gameId"])
        if not game or game["state"] == "pre":
            continue
        if game["state"] == "in":
            any_live = True
        item = {
            "id": entry["id"],
            "player": entry.get("player"),
            "marketKey": entry.get("marketKey"),
            "line": entry.get("line"),
            "side": entry.get("side"),
            "state": game["state"],
            "detail": game.get("status_detail"),
            "score": (
                f"{game['away_team']} {game['away_score'] or 0} - "
                f"{game['home_team']} {game['home_score'] or 0}"
            ),
        }
        if entry["marketKey"] in GAME_MARKETS:
            home_score, away_score = game.get("home_score") or 0, game.get("away_score") or 0
            item["current"] = (
                home_score + away_score
                if entry["marketKey"] == "total"
                else (home_score if entry.get("side") == "home" else away_score)
            )
        else:
            row = next(
                (
                    stat for stat in nfl_data.live_game_stats(game)
                    if stat["player_id"] == str(entry.get("playerId"))
                ),
                None,
            )
            if row:
                if entry["marketKey"] == "anytime_td":
                    item["current"] = row["rushing_tds"] + row["receiving_tds"]
                else:
                    item["current"] = row.get(PROP_STAT.get(entry["marketKey"], ""), 0)
            else:
                item["current"] = 0
        if isinstance(item.get("current"), (int, float)) and entry.get("line") is not None:
            over = item["current"] > float(entry["line"])
            item["hit"] = over if entry.get("side") == "over" else not over
        picks.append(item)
    return {"live": any_live, "picks": picks}


# -------------------------------------------------------------------- summary

def _entry_probability(entry: dict) -> float | None:
    for key in ("consensusProb", "modelProb"):
        value = entry.get(key)
        if isinstance(value, (int, float)):
            return max(0.0, min(1.0, float(value)))
    return None


def _ece(samples: list[tuple[float, float]], bins: int = 10) -> float | None:
    if not samples:
        return None
    total = len(samples)
    error = 0.0
    for index in range(bins):
        low, high = index / bins, (index + 1) / bins
        bucket = [
            sample for sample in samples
            if low <= sample[0] < high or (index == bins - 1 and sample[0] == 1.0)
        ]
        if not bucket:
            continue
        mean_probability = sum(item[0] for item in bucket) / len(bucket)
        mean_outcome = sum(item[1] for item in bucket) / len(bucket)
        error += len(bucket) / total * abs(mean_probability - mean_outcome)
    return round(error, 6)


def performance_summary() -> dict:
    store = _load()
    entries = [entry for day in store.values() for entry in day.get("entries", [])]
    graded = [entry for entry in entries if entry.get("grade") in ("win", "loss", "push")]
    wins = sum(1 for entry in graded if entry["grade"] == "win")
    losses = sum(1 for entry in graded if entry["grade"] == "loss")
    pushes = sum(1 for entry in graded if entry["grade"] == "push")
    profit = round(sum(entry.get("profitDollars") or 0 for entry in graded), 2)
    staked = sum(float(entry.get("stakeDollars") or 0) for entry in graded)
    with_clv = [entry for entry in entries if isinstance(entry.get("clvEdge"), (int, float))]
    beat = sum(1 for entry in with_clv if entry["clvEdge"] > 0)
    calibration: list[tuple[float, float]] = []
    briers: list[float] = []
    for entry in graded:
        if entry.get("grade") not in {"win", "loss"}:
            continue
        probability = _entry_probability(entry)
        if probability is None:
            continue
        outcome = 1.0 if entry["grade"] == "win" else 0.0
        calibration.append((probability, outcome))
        briers.append((probability - outcome) ** 2)

    per_market: dict[str, dict] = {}
    for entry in graded:
        market = per_market.setdefault(
            entry.get("marketKey") or "?",
            {"n": 0, "wins": 0, "losses": 0, "pushes": 0, "profit": 0.0},
        )
        market["n"] += 1
        market[entry["grade"] + ("s" if entry["grade"] != "loss" else "es")] += 1
        market["profit"] = round(market["profit"] + (entry.get("profitDollars") or 0), 2)
    decided = wins + losses
    return {
        "primaryKpi": {
            "metric": "clv",
            "value": round(beat / len(with_clv), 4) if with_clv else None,
            "avg_clv": (
                round(sum(entry["clvEdge"] for entry in with_clv) / len(with_clv), 4)
                if with_clv else None
            ),
            "n": len(with_clv),
        },
        "picks": len(entries),
        "pending": len(entries) - len(graded),
        "wins": wins,
        "losses": losses,
        "pushes": pushes,
        "hit_rate": round(wins / decided, 4) if decided else None,
        "profitDollars": profit,
        "roi": round(profit / staked, 4) if staked else None,
        "brier": round(sum(briers) / len(briers), 6) if briers else None,
        "ece": _ece(calibration),
        "calibrationSamples": len(calibration),
        "receiptCoverage": round(
            sum(bool(entry.get("releaseFingerprint")) for entry in entries) / len(entries), 4
        ) if entries else 1.0,
        "per_market": per_market,
        "settings": get_settings(),
        "persistence": persistence_status(),
    }


# -------------------------------------------------------------------- workers

_workers_started = False


def start_background_workers() -> None:
    """Auto-grade + closing-capture loops. Called once from app preload."""
    global _workers_started
    if _workers_started:
        return
    _workers_started = True

    def _grade_loop():
        while True:
            time.sleep(AUTO_SYNC_MIN * 60)
            try:
                grade_pending()
                # Import here to avoid creating a circular dependency during app
                # registration. Publication receipts are graded alongside user
                # tracked picks, but their release payload remains immutable.
                import decision_ledger

                decision_ledger.grade_pending()
            except Exception as exc:  # noqa: BLE001
                print(f"[tracker] grade loop: {exc}")

    def _closing_loop():
        while True:
            time.sleep(CLOSING_INTERVAL_MIN * 60)
            try:
                closing_capture_once()
            except Exception as exc:  # noqa: BLE001
                print(f"[tracker] closing loop: {exc}")

    threading.Thread(target=_grade_loop, daemon=True, name="tracker-grade").start()
    if CLOSING_ENABLED:
        threading.Thread(target=_closing_loop, daemon=True, name="tracker-closing").start()
