"""
StatMuse-style natural-language stat queries — answered from OUR data.

Deterministic parser + executor over the nfl_data caches (player game logs,
schedules, team summaries). No LLM, no external service, no scraping: every
answer is computed from the same warehouse the rest of the app uses, and the
response always includes the parsed interpretation so the user can see
exactly what was answered.

Supported question shapes (v1):
  player stat:   "CeeDee Lamb receiving yards"
                 "Saquon Barkley rushing yards last 4 games"
                 "Josh Allen pass TDs at home per game"
                 "Lamb receptions vs PHI"  /  "... week 5"
  leaders:       "who leads the NFL in receiving TDs"
                 "top 5 rushing yards per game"  /  "most receptions by a TE"
  team:          "Eagles record"  /  "PHI points per game"
  game result:   "Eagles week 1 score"  /  "Cowboys last game"

Self-test: python stat_query.py   (needs the data/ caches)
"""
from __future__ import annotations

import difflib
import re
import unicodedata

import nfl_data

# ------------------------------------------------------------------ stats map

# canonical -> (columns summed, label, is_count)
STATS: dict[str, tuple[tuple[str, ...], str]] = {
    "passing_yards":   (("passing_yards",), "passing yards"),
    "passing_tds":     (("passing_tds",), "passing TDs"),
    "interceptions":   (("interceptions",), "interceptions"),
    "completions":     (("completions",), "completions"),
    "attempts":        (("attempts",), "pass attempts"),
    "sacks":           (("sacks",), "sacks taken"),
    "rushing_yards":   (("rushing_yards",), "rushing yards"),
    "rushing_tds":     (("rushing_tds",), "rushing TDs"),
    "carries":         (("carries",), "carries"),
    "receiving_yards": (("receiving_yards",), "receiving yards"),
    "receiving_tds":   (("receiving_tds",), "receiving TDs"),
    "receptions":      (("receptions",), "receptions"),
    "targets":         (("targets",), "targets"),
    "fumbles_lost":    (("fumbles_lost",), "fumbles lost"),
    "scrimmage_yards": (("rushing_yards", "receiving_yards"), "scrimmage yards"),
    "total_tds":       (("rushing_tds", "receiving_tds"), "total TDs"),
    "total_yards":     (("passing_yards", "rushing_yards", "receiving_yards"),
                        "total yards"),
}

# phrase -> canonical. Matched longest-first against the normalized question.
SYNONYMS: dict[str, str] = {
    "passing yards": "passing_yards", "pass yards": "passing_yards",
    "pass yds": "passing_yards", "passing yds": "passing_yards",
    "passing touchdowns": "passing_tds", "passing tds": "passing_tds",
    "pass tds": "passing_tds", "pass td": "passing_tds",
    "touchdown passes": "passing_tds", "td passes": "passing_tds",
    "interceptions": "interceptions", "ints": "interceptions",
    "picks": "interceptions",
    "completions": "completions", "pass attempts": "attempts",
    "sacks": "sacks",
    "rushing yards": "rushing_yards", "rush yards": "rushing_yards",
    "rush yds": "rushing_yards", "rushing yds": "rushing_yards",
    "rushing touchdowns": "rushing_tds", "rushing tds": "rushing_tds",
    "rush tds": "rushing_tds", "rush td": "rushing_tds",
    "carries": "carries", "rush attempts": "carries",
    "receiving yards": "receiving_yards", "rec yards": "receiving_yards",
    "rec yds": "receiving_yards", "receiving yds": "receiving_yards",
    "receiving touchdowns": "receiving_tds", "receiving tds": "receiving_tds",
    "rec tds": "receiving_tds", "rec td": "receiving_tds",
    "receptions": "receptions", "catches": "receptions",
    "targets": "targets", "fumbles lost": "fumbles_lost",
    "fumbles": "fumbles_lost",
    "scrimmage yards": "scrimmage_yards",
    "yards from scrimmage": "scrimmage_yards",
    "total touchdowns": "total_tds", "total tds": "total_tds",
    "touchdowns": "total_tds", "tds": "total_tds",
    "total yards": "total_yards",
    # bare "yards" resolved contextually by position later
    "yards": "_yards_contextual",
}

_POS_DEFAULT_YARDS = {"QB": "passing_yards", "RB": "rushing_yards",
                      "WR": "receiving_yards", "TE": "receiving_yards"}
_POS_WORDS = {"qb": "QB", "quarterback": "QB", "rb": "RB", "running back": "RB",
              "wr": "WR", "receiver": "WR", "wide receiver": "WR",
              "te": "TE", "tight end": "TE"}

TEAM_NAMES = {
    "ARI": ("arizona", "cardinals"), "ATL": ("atlanta", "falcons"),
    "BAL": ("baltimore", "ravens"), "BUF": ("buffalo", "bills"),
    "CAR": ("carolina", "panthers"), "CHI": ("chicago", "bears"),
    "CIN": ("cincinnati", "bengals"), "CLE": ("cleveland", "browns"),
    "DAL": ("dallas", "cowboys"), "DEN": ("denver", "broncos"),
    "DET": ("detroit", "lions"), "GB": ("green bay", "packers"),
    "HOU": ("houston", "texans"), "IND": ("indianapolis", "colts"),
    "JAX": ("jacksonville", "jaguars"), "KC": ("kansas city", "chiefs"),
    "LAC": ("chargers",), "LAR": ("rams",), "LV": ("las vegas", "raiders"),
    "MIA": ("miami", "dolphins"), "MIN": ("minnesota", "vikings"),
    "NE": ("new england", "patriots"), "NO": ("new orleans", "saints"),
    "NYG": ("giants",), "NYJ": ("jets",), "PHI": ("philadelphia", "eagles"),
    "PIT": ("pittsburgh", "steelers"), "SEA": ("seattle", "seahawks"),
    "SF": ("san francisco", "49ers", "niners"), "TB": ("tampa bay", "buccaneers", "bucs"),
    "TEN": ("tennessee", "titans"), "WSH": ("washington", "commanders"),
}

_LEADER_CUES = ("who leads", "who led", "leader", "leaders", "most ", "top ",
                "best ", "highest")
_FILLER = re.compile(
    r"\b(how many|how much|what|whats|what's|did|does|do|have|has|had|the|nfl|"
    r"in|of|for|a|an|this season|season|stats?|is|are|show me|show|me|get)\b")


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9 ]", " ", s.lower()).strip()


# ------------------------------------------------------------------- parsing

def _extract_scope(q: str) -> tuple[dict, str]:
    scope: dict = {}
    m = re.search(r"\b(?:in )?(20\d{2})\b", q)
    if m:
        scope["season"] = int(m.group(1))
        q = q.replace(m.group(0), " ")
    m = re.search(r"last (\d+)(?: games?)?", q)
    if m:
        scope["last_n"] = int(m.group(1))
        q = q.replace(m.group(0), " ")
    m = re.search(r"week (\d+)", q)
    if m:
        scope["week"] = int(m.group(1))
        q = q.replace(m.group(0), " ")
    if re.search(r"\bat home\b|\bhome\b", q):
        scope["home"] = 1
        q = re.sub(r"\bat home\b|\bhome\b", " ", q)
    if re.search(r"\baway\b|\bon the road\b", q):
        scope["home"] = 0
        q = re.sub(r"\baway\b|\bon the road\b", " ", q)
    if re.search(r"\bper game\b|\baverage\b|\bavg\b", q):
        scope["per_game"] = True
        q = re.sub(r"\bper game\b|\baverage\b|\bavg\b", " ", q)
    m = re.search(r"\b(?:vs|versus|against) ([a-z0-9 ]+)$", q.strip())
    if m:
        opp = _match_team(m.group(1).strip())
        if opp:
            scope["opponent"] = opp
            q = q[:m.start()] + " "
    return scope, q


def _extract_stat(q: str) -> tuple[str | None, str]:
    for phrase in sorted(SYNONYMS, key=len, reverse=True):
        if re.search(rf"\b{re.escape(phrase)}\b", q):
            return SYNONYMS[phrase], re.sub(rf"\b{re.escape(phrase)}\b", " ", q, count=1)
    return None, q


def _match_team(text: str) -> str | None:
    t = _norm(text)
    if t.upper() in TEAM_NAMES or t.upper() in ("GB", "KC", "LV", "NE", "NO", "SF", "TB"):
        return t.upper() if t.upper() in TEAM_NAMES else None
    if len(t) <= 3 and t.upper() in TEAM_NAMES:
        return t.upper()
    for abbr, names in TEAM_NAMES.items():
        if t == abbr.lower():
            return abbr
        for n in names:
            if n in t or t == n:
                return abbr
    return None


def _match_player(text: str, season: int) -> dict | None:
    t = _norm(text).strip()
    if len(t) < 3:
        return None
    idx = nfl_data.player_index(season)
    by_name = {_norm(m["name"]): m for m in idx.values()}
    if t in by_name:
        return by_name[t]
    # try contains (last-name queries: "lamb receiving yards")
    contains = [m for k, m in by_name.items()
                if t in k or all(w in k for w in t.split())]
    if len(contains) == 1:
        return contains[0]
    if contains:  # prefer the one with most games (starters over fringe)
        return max(contains, key=lambda m: m["games"])
    close = difflib.get_close_matches(t, by_name.keys(), n=1, cutoff=0.72)
    return by_name[close[0]] if close else None


# ----------------------------------------------------------------- execution

def _stat_value(row: dict, cols: tuple[str, ...]) -> float:
    return sum(row[c] for c in cols)


def _apply_scope(rows: list[dict], scope: dict) -> list[dict]:
    out = rows
    if "week" in scope:
        out = [r for r in out if r["week"] == scope["week"]
               and r["season_type"] == "REG"]
    if "home" in scope:
        out = [r for r in out if r["home"] == scope["home"]]
    if "opponent" in scope:
        out = [r for r in out if r["opponent"] == scope["opponent"]]
    if "last_n" in scope:
        out = out[-scope["last_n"]:]
    return out


def _scope_phrase(scope: dict, season: int) -> str:
    bits = []
    if "week" in scope:
        bits.append(f"Week {scope['week']}")
    if "last_n" in scope:
        bits.append(f"last {scope['last_n']} games")
    if scope.get("home") == 1:
        bits.append("at home")
    if scope.get("home") == 0:
        bits.append("on the road")
    if "opponent" in scope:
        bits.append(f"vs {scope['opponent']}")
    bits.append(str(season))
    return " · ".join(bits)


def _game_table(rows: list[dict], cols: tuple[str, ...]) -> dict:
    return {
        "columns": ["Week", "Opp", "Value"],
        "rows": [[("PO" if r["season_type"] == "POST" else "") + str(r["week"]),
                  ("vs " if r["home"] else "@ ") + (r["opponent"] or "?"),
                  round(_stat_value(r, cols), 1)] for r in rows],
    }


def _answer_player_stat(player: dict, stat: str, scope: dict, season: int) -> dict:
    cols, label = STATS[stat]
    logs = nfl_data.player_game_logs(season).get(player["player_id"], [])
    rows = _apply_scope(logs, scope)
    if not rows:
        return {"ok": False, "error": f"No games found for {player['name']} with those filters in {season}."}
    total = round(sum(_stat_value(r, cols) for r in rows), 1)
    per_game = round(total / len(rows), 2)
    per = scope.get("per_game")
    value = per_game if per else (int(total) if total == int(total) else total)
    if len(rows) == 1:
        r = rows[0]
        where = ("vs " if r["home"] else "@ ") + (r["opponent"] or "?")
        headline = (f"{player['name']} had {value} {label} in Week {r['week']} "
                    f"{where} ({season}).")
    elif per:
        headline = (f"{player['name']} averaged {value} {label} per game "
                    f"({len(rows)} games, {season}).")
    else:
        headline = (f"{player['name']} had {value} {label} over "
                    f"{len(rows)} games in {season}.")
    return {
        "ok": True, "kind": "player_stat",
        "headline": headline, "value": value,
        "interpreted": f"{player['name']} · {label} · {_scope_phrase(scope, season)}"
                       + (" · per game" if per else ""),
        "player": {"id": player["player_id"], "name": player["name"],
                   "team": player["team"], "position": player["position"]},
        "summary": {"total": total, "per_game": per_game, "games": len(rows)},
        "table": _game_table(rows, cols),
    }


def _answer_leaders(stat: str, scope: dict, season: int, top_n: int,
                    position: str | None) -> dict:
    cols, label = STATS[stat]
    idx = nfl_data.player_index(season)
    logs = nfl_data.player_game_logs(season)
    board = []
    for pid, meta in idx.items():
        if position and meta["position"] != position:
            continue
        rows = _apply_scope(logs.get(pid, []), scope)
        if not rows:
            continue
        total = sum(_stat_value(r, cols) for r in rows)
        if total <= 0:
            continue
        board.append((total, round(total / len(rows), 2), len(rows), meta))
    if not board:
        return {"ok": False, "error": f"No qualifying players for {label} in {season}."}
    per = scope.get("per_game")
    board.sort(key=lambda x: (x[1] if per else x[0]), reverse=True)
    board = board[:top_n]
    lead = board[0]
    lead_val = lead[1] if per else (int(lead[0]) if lead[0] == int(lead[0]) else round(lead[0], 1))
    pos_txt = f" among {position}s" if position else ""
    headline = (f"{lead[3]['name']} leads{pos_txt} with {lead_val} {label}"
                + (" per game" if per else "") + f" in {season}.")
    return {
        "ok": True, "kind": "leaders",
        "headline": headline,
        "interpreted": f"Leaders · {label}" + (f" · {position}" if position else "")
                       + (" · per game" if per else "")
                       + f" · {_scope_phrase(scope, season)}",
        "table": {
            "columns": ["#", "Player", "Team", "Pos",
                        "Per game" if per else "Total", "Games"],
            "rows": [[i + 1,
                      {"text": b[3]["name"], "link": f"/player/{b[3]['player_id']}"},
                      b[3]["team"], b[3]["position"],
                      b[1] if per else (int(b[0]) if b[0] == int(b[0]) else round(b[0], 1)),
                      b[2]] for i, b in enumerate(board)],
        },
    }


def _answer_team(team: str, q: str, season: int) -> dict | None:
    ts = nfl_data.team_summaries(season).get(team)
    if not ts:
        return None
    if re.search(r"\brecord\b|\bwins\b|\blosses\b", q):
        return {"ok": True, "kind": "team",
                "headline": f"The {team} went {ts['record']} in {season} "
                            f"({ts['ppg']} PF/g, {ts['papg']} PA/g).",
                "interpreted": f"{team} · record · {season}",
                "summary": ts}
    if re.search(r"points per game|ppg|scoring", q):
        return {"ok": True, "kind": "team",
                "headline": f"The {team} scored {ts['ppg']} points per game in "
                            f"{season} and allowed {ts['papg']}.",
                "interpreted": f"{team} · points per game · {season}",
                "summary": ts}
    if re.search(r"\bscore\b|\bresult\b|\blast game\b|\bgame\b", q):
        games = [g for g in nfl_data.get_schedule(season)
                 if team in (g["home_team"], g["away_team"]) and g["completed"]]
        m = re.search(r"week (\d+)", q)
        game = None
        if m:
            wk = int(m.group(1))
            game = next((g for g in games if g["week"] == wk
                         and g["season_type"] == "REG"), None)
        elif games:
            game = games[-1]
        if game:
            return {"ok": True, "kind": "game",
                    "headline": f"{game['away_team']} {game['away_score']} — "
                                f"{game['home_team']} {game['home_score']} "
                                f"(Week {game['week']}, {season}).",
                    "interpreted": f"{team} · game result · week {game['week']} · {season}",
                    "game": {"id": game["game_id"], "link": f"/game/{game['game_id']}?season={season}"}}
    # bare team question -> record
    return {"ok": True, "kind": "team",
            "headline": f"The {team} went {ts['record']} in {season} "
                        f"({ts['ppg']} PF/g, {ts['papg']} PA/g).",
            "interpreted": f"{team} · overview · {season}",
            "summary": ts}


EXAMPLES = [
    "CeeDee Lamb receiving yards",
    "Saquon Barkley rushing yards last 4 games",
    "Josh Allen passing TDs at home",
    "who leads the NFL in receiving TDs",
    "top 5 rushing yards per game",
    "most receptions by a TE",
    "Eagles record",
    "Cowboys week 1 score",
]


def ask(question: str, season: int | None = None) -> dict:
    """Answer a natural-language stat question from our own data."""
    raw = question or ""
    q = _norm(raw)
    if not q:
        return {"ok": False, "error": "Ask a question.", "examples": EXAMPLES}
    scope, q = _extract_scope(q)
    season = scope.get("season") or season or nfl_data.stats_season()
    scope.pop("season", None)

    leaders = any(c in q for c in _LEADER_CUES)
    m = re.search(r"top (\d+)", q)
    top_n = int(m.group(1)) if m else 10
    position = None
    for w, pos in sorted(_POS_WORDS.items(), key=lambda x: -len(x[0])):
        if re.search(rf"\b{w}s?\b", q):
            position = pos
            q = re.sub(rf"\bby a {w}\b|\b{w}s?\b", " ", q)
            break

    stat, q = _extract_stat(q)
    q = _FILLER.sub(" ", q)
    q = re.sub(r"\b(who leads|who led|leaders?|most|top \d+|top|best|highest)\b", " ", q)
    q = re.sub(r"\s+", " ", q).strip()

    if stat == "_yards_contextual":
        stat = None  # resolve after we know the player's position

    if leaders:
        if not stat:
            return {"ok": False, "error": "Which stat? e.g. \"who leads the NFL in rushing yards\".",
                    "examples": EXAMPLES}
        if stat == "_yards_contextual":
            stat = "scrimmage_yards"
        return _answer_leaders(stat, scope, season, top_n, position)

    # team question?
    team = _match_team(q) if q else None
    if team and not stat:
        ans = _answer_team(team, _norm(raw), season)
        if ans:
            return ans

    player = _match_player(q, season) if q else None
    if player:
        if not stat:
            stat = _POS_DEFAULT_YARDS.get(player["position"], "scrimmage_yards")
        elif stat == "_yards_contextual":
            stat = _POS_DEFAULT_YARDS.get(player["position"], "scrimmage_yards")
        return _answer_player_stat(player, stat, scope, season)

    if team:
        ans = _answer_team(team, _norm(raw), season)
        if ans:
            return ans

    return {"ok": False,
            "error": "Couldn't match that to a player, team, or stat question.",
            "examples": EXAMPLES}


# ------------------------------------------------------------------ selftest

def _selftest() -> None:
    season = nfl_data.stats_season()
    checks = [
        ("CeeDee Lamb receiving yards", "player_stat"),
        ("lamb receptions at home", "player_stat"),
        ("Saquon Barkley rushing yards last 4 games", "player_stat"),
        ("who leads the nfl in receiving tds", "leaders"),
        ("top 5 rushing yards per game", "leaders"),
        ("most receptions by a te", "leaders"),
        ("Eagles record", "team"),
        ("cowboys week 1 score", "game"),
    ]
    for q, kind in checks:
        a = ask(q, season)
        assert a.get("ok"), (q, a.get("error"))
        assert a["kind"] == kind, (q, a["kind"], kind)
        print(f"  OK [{a['kind']:11s}] {q!r} -> {a['headline']}")
    bad = ask("what is the meaning of football")
    assert not bad["ok"] and bad.get("examples")
    print("stat_query self-test OK")


if __name__ == "__main__":
    _selftest()
