"""End-to-end exercise of the odds path, without spending Odds API credits.

Nothing in the suite covered what happens once ODDS_API_KEY is set: the board
had only ever been observed in its degraded, price-free state. These stub the
HTTP layer and the per-event props so the whole chain — parse, de-vig, edge,
EV, Kelly, grade — runs the way it will in production.
"""

import json

import odds_api
import projections as pj
import value_engine as ve


def _american(price):
    return {"price": price}


def _event_props(player, market_key, line, over=-110, under=-110):
    """One Odds API event-odds payload, shaped as the real endpoint returns it."""
    outcomes = [
        {"name": "Over", "description": player, "point": line, "price": over},
        {"name": "Under", "description": player, "point": line, "price": under},
    ]
    return {
        "id": "evt-test",
        "bookmakers": [
            {
                "key": "draftkings",
                "title": "DraftKings",
                "markets": [{"key": market_key, "outcomes": outcomes}],
            },
            {"key": "fanduel", "title": "FanDuel", "markets": [{"key": market_key, "outcomes": outcomes}]},
        ],
    }


# --------------------------------------------------------------- parsing


def test_parse_prop_markets_reads_over_under_rows():
    rows = odds_api.parse_prop_markets(_event_props("Drake Maye", "player_pass_yds", 219.5))
    assert len(rows) == 4  # two books x two sides
    over = [r for r in rows if r["side"] == "over"]
    assert {r["book"] for r in over} == {"DraftKings", "FanDuel"}
    assert all(r["line"] == 219.5 and r["base_key"] == "player_pass_yds" for r in over)
    assert all(not r["is_alt"] for r in rows)


def test_parse_prop_markets_maps_anytime_td_yes_no_to_over_under():
    """Anytime TD is Yes/No shaped, and must land on the 0.5 line."""
    payload = {
        "bookmakers": [
            {
                "title": "DraftKings",
                "markets": [
                    {
                        "key": "player_anytime_td",
                        "outcomes": [
                            {"name": "Yes", "description": "Rhamondre Stevenson", "price": 120},
                            {"name": "No", "description": "Rhamondre Stevenson", "price": -150},
                        ],
                    }
                ],
            }
        ]
    }
    rows = odds_api.parse_prop_markets(payload)
    assert {r["side"] for r in rows} == {"over", "under"}
    assert all(r["line"] == 0.5 for r in rows)


def test_alternate_markets_are_flagged():
    rows = odds_api.parse_prop_markets(_event_props("Drake Maye", "player_pass_yds_alternate", 249.5))
    assert rows and all(r["is_alt"] and r["base_key"] == "player_pass_yds" for r in rows)


def test_every_prop_market_maps_to_a_model_market():
    """A market the model cannot price would silently drop its book rows."""
    for key in odds_api.PROP_MARKETS:
        assert key in pj.ODDS_KEY_TO_MARKET, f"{key} has no model market"


def test_parse_game_markets_reads_the_three_featured_books():
    ev = {
        "home_team": "Seattle Seahawks",
        "away_team": "New England Patriots",
        "bookmakers": [
            {
                "title": "DraftKings",
                "markets": [
                    {
                        "key": "h2h",
                        "outcomes": [
                            {"name": "Seattle Seahawks", "price": -160},
                            {"name": "New England Patriots", "price": 135},
                        ],
                    },
                    {
                        "key": "spreads",
                        "outcomes": [
                            {"name": "Seattle Seahawks", "point": -3.5, "price": -110},
                            {"name": "New England Patriots", "point": 3.5, "price": -110},
                        ],
                    },
                    {
                        "key": "totals",
                        "outcomes": [
                            {"name": "Over", "point": 44.5, "price": -105},
                            {"name": "Under", "point": 44.5, "price": -115},
                        ],
                    },
                ],
            }
        ],
    }
    out = odds_api.parse_game_markets(ev)
    assert out["h2h"][0]["home_price"] == -160
    assert out["spreads"][0]["home_point"] == -3.5
    assert out["totals"][0]["point"] == 44.5


# --------------------------------------------------------- credit hygiene


def test_game_odds_are_fetched_once_per_day(monkeypatch, tmp_path):
    """One frozen snapshot per day is the credit-hygiene invariant."""
    calls = []

    class _Resp:
        status_code = 200

        @staticmethod
        def json():
            return [
                {
                    "id": "evt-1",
                    "home_team": "Seattle Seahawks",
                    "away_team": "New England Patriots",
                    "bookmakers": [],
                }
            ]

    def _fake_get(url, **kwargs):
        calls.append(url)
        return _Resp()

    monkeypatch.setenv("ODDS_API_KEY", "test-key-not-real")
    monkeypatch.setattr(odds_api.http_client, "get", _fake_get)
    monkeypatch.setattr(odds_api, "_CACHE_FILE", str(tmp_path / "odds_cache.json"))
    monkeypatch.setattr(odds_api, "_snapshot", None)

    assert odds_api.is_configured()
    first = odds_api.get_game_odds()
    assert first and len(calls) == 1
    # Second call inside the TTL must be served from the snapshot.
    odds_api.get_game_odds()
    assert len(calls) == 1, "a second call re-spent Odds API credits"
    # ...and the snapshot must survive a restart.
    saved = json.loads((tmp_path / "odds_cache.json").read_text())
    assert saved["game_odds"]["events"][0]["id"] == "evt-1"


def test_without_a_key_nothing_is_fetched(monkeypatch):
    calls = []
    monkeypatch.delenv("ODDS_API_KEY", raising=False)
    monkeypatch.setattr(odds_api.http_client, "get", lambda *a, **k: calls.append(1))
    assert odds_api.is_configured() is False
    assert odds_api.get_game_odds() == []
    assert not calls, "an unconfigured app must not call the Odds API"


# ------------------------------------------------------- pricing the board


def test_devig_and_edge_math_on_a_balanced_market():
    """Two -110 sides de-vig to a fair 50/50, so a 58% model has ~8 points edge."""
    fair = ve.fair_prob(-110, -110)
    assert abs(fair - 0.5) < 1e-9
    implied = ve.american_to_implied(-110)
    assert abs(implied - 0.5238) < 0.001
    edge = 0.58 - implied
    assert 0.05 < edge < 0.06
    assert ve.expected_value(0.58, -110) > 0
    assert ve.kelly_stake(0.58, -110)["stake_pct"] > 0
    assert ve.edge_grade(edge) is not None


def test_a_priced_market_produces_a_graded_row(monkeypatch, client):
    """The board must fill edge, EV, Kelly and a grade once prices exist."""
    import routes.props as props

    captured = {}
    real_build = props._build_game_rows

    monkeypatch.setattr(odds_api, "is_configured", lambda: True)
    monkeypatch.setattr(odds_api, "find_event_for_game", lambda game: {"id": "evt-test"})

    def _props_for(event_id, force=False):
        # Price every market the model asks about, at a balanced -110/-110.
        books = []
        for okey in odds_api.PROP_MARKETS:
            outcomes = []
            for name in captured.get("players", []):
                line = captured["lines"].get((name, pj.ODDS_KEY_TO_MARKET[okey]))
                if line is None:
                    continue
                if okey == "player_anytime_td":
                    outcomes += [
                        {"name": "Yes", "description": name, "price": 120},
                        {"name": "No", "description": name, "price": -150},
                    ]
                else:
                    outcomes += [
                        {"name": "Over", "description": name, "point": line, "price": -110},
                        {"name": "Under", "description": name, "point": line, "price": -110},
                    ]
            if outcomes:
                books.append({"key": okey, "outcomes": outcomes})
        return {
            "id": event_id,
            "bookmakers": [{"key": "draftkings", "title": "DraftKings", "markets": books}],
        }

    def _spy(game, season):
        # First pass with no odds tells us the players and lines in play.
        monkeypatch.setattr(odds_api, "is_configured", lambda: False)
        base = real_build(game, season)
        captured["players"] = [r["player"] for r in base]
        captured["lines"] = {(r["player"], r["marketKey"]): r["line"] for r in base}
        monkeypatch.setattr(odds_api, "is_configured", lambda: True)
        monkeypatch.setattr(odds_api, "get_event_props", _props_for)
        return real_build(game, season)

    monkeypatch.setattr(props, "_build_game_rows", _spy)
    props._RESP_CACHE.clear()

    resp = client.get("/api/props/board")
    assert resp.status_code == 200
    rows = resp.get_json()["rows"]
    priced = [r for r in rows if r.get("bestOver") or r.get("bestUnder")]
    assert priced, "no row picked up a book price"
    for r in priced[:20]:
        assert r["impliedProb"] is not None
        assert r["edge"] is not None
        assert r["evPct"] is not None
        assert r["kellyPct"] is not None
        assert r["noOdds"] is False
    assert any(r["grade"] for r in priced), "no row was graded"


# --------------------------------------------------- matching games to events


def _game(home_name, away_name):
    return {"home_name": home_name, "away_name": away_name}


def test_event_matching_prefers_the_exact_full_name(monkeypatch):
    events = [
        {"id": "wrong", "home_team": "Los Angeles Chargers", "away_team": "New England Patriots"},
        {"id": "right", "home_team": "Seattle Seahawks", "away_team": "New England Patriots"},
    ]
    monkeypatch.setattr(odds_api, "get_game_odds", lambda: events)
    hit = odds_api.find_event_for_game(_game("Seattle Seahawks", "New England Patriots"))
    assert hit["id"] == "right"


def test_event_matching_survives_a_different_city_spelling(monkeypatch):
    """A priced board must not read as empty because two feeds spell LA differently."""
    monkeypatch.setattr(
        odds_api,
        "get_game_odds",
        lambda: [
            {"id": "evt", "home_team": "LA Rams", "away_team": "SF 49ers"},
        ],
    )
    hit = odds_api.find_event_for_game(_game("Los Angeles Rams", "San Francisco 49ers"))
    assert hit is not None and hit["id"] == "evt"


def test_event_matching_still_refuses_a_genuine_mismatch(monkeypatch):
    monkeypatch.setattr(
        odds_api,
        "get_game_odds",
        lambda: [
            {"id": "evt", "home_team": "Seattle Seahawks", "away_team": "Arizona Cardinals"},
        ],
    )
    assert odds_api.find_event_for_game(_game("Seattle Seahawks", "New England Patriots")) is None


def test_event_matching_does_not_swap_home_and_away(monkeypatch):
    """The reversed fixture is a different game and must not match."""
    monkeypatch.setattr(
        odds_api,
        "get_game_odds",
        lambda: [
            {"id": "evt", "home_team": "New England Patriots", "away_team": "Seattle Seahawks"},
        ],
    )
    assert odds_api.find_event_for_game(_game("Seattle Seahawks", "New England Patriots")) is None


def test_nfl_nicknames_are_unique_so_the_fallback_is_unambiguous():
    """The fallback is only safe while no two clubs share a last word."""
    import nfl_data

    names = {}
    for g in nfl_data.get_schedule(nfl_data.default_season()):
        for side in ("home", "away"):
            if g.get(f"{side}_name"):
                names[g[f"{side}_team"]] = g[f"{side}_name"]
    nicks = [odds_api._nickname(n) for n in names.values()]
    assert len(nicks) == len(set(nicks)), "two clubs share a nickname; the fallback is unsafe"


# ------------------------------------------------- closing capture, once only


def _pending_pick(game_id="401872656", **over):
    pick = {
        "id": "p1",
        "gameId": game_id,
        "season": 2026,
        "grade": "pending",
        "marketKey": "pass_yds",
        "line": 219.5,
        "side": "over",
        "price": -110,
        "player": "Drake Maye",
        "closingPrice": None,
        "openingImplied": 0.5238,
    }
    pick.update(over)
    return pick


def _closing_env(monkeypatch, tmp_path, picks, live_payload):
    """Point the tracker at a scratch store with one game inside its window."""
    import tracker

    store = {"2026-09-09": {"entries": picks}}
    saved = {}
    monkeypatch.setattr(tracker, "_load", lambda: store)
    monkeypatch.setattr(tracker, "_save", lambda s: saved.update({"store": s}))
    monkeypatch.setattr(tracker, "_kickoff_window", lambda game: True)
    monkeypatch.setattr(tracker, "_closing_captured", set())
    monkeypatch.setattr(
        tracker.nfl_data,
        "get_schedule",
        lambda season: [
            {"game_id": "401872656", "home_name": "Seattle Seahawks", "away_name": "New England Patriots"}
        ],
    )
    monkeypatch.setattr(tracker.odds_api, "is_configured", lambda: True)
    monkeypatch.setattr(tracker.odds_api, "find_event_for_game", lambda g: {"id": "evt"})
    calls = []

    def _live(event_id, markets=None):
        calls.append(event_id)
        return live_payload

    monkeypatch.setattr(tracker.odds_api, "fetch_event_odds_live", _live)
    return tracker, store, calls


def test_closing_capture_buys_a_game_once_even_when_no_line_matches(monkeypatch, tmp_path):
    """The book moving off the pick's number must not re-buy every cycle."""
    payload = {
        "bookmakers": [
            {
                "title": "DraftKings",
                "markets": [
                    {
                        "key": "player_pass_yds",
                        "outcomes": [
                            # A different line than the pick's 219.5, so nothing matches.
                            {"name": "Over", "description": "Drake Maye", "point": 244.5, "price": -110},
                            {"name": "Under", "description": "Drake Maye", "point": 244.5, "price": -110},
                        ],
                    }
                ],
            }
        ]
    }
    tracker, store, calls = _closing_env(monkeypatch, tmp_path, [_pending_pick()], payload)

    tracker.closing_capture_once()
    assert len(calls) == 1
    assert store["2026-09-09"]["closingAttempted"] == ["401872656"]

    # A restart clears the in-process guard; the stored attempt must still hold.
    monkeypatch.setattr(tracker, "_closing_captured", set())
    tracker.closing_capture_once()
    assert len(calls) == 1, "a restart re-bought closing odds already paid for"


def test_closing_capture_records_price_and_clv_when_the_line_matches(monkeypatch, tmp_path):
    payload = {
        "bookmakers": [
            {
                "title": "DraftKings",
                "markets": [
                    {
                        "key": "player_pass_yds",
                        "outcomes": [
                            {"name": "Over", "description": "Drake Maye", "point": 219.5, "price": -140},
                            {"name": "Under", "description": "Drake Maye", "point": 219.5, "price": 115},
                        ],
                    }
                ],
            }
        ]
    }
    pick = _pending_pick()
    tracker, store, calls = _closing_env(monkeypatch, tmp_path, [pick], payload)

    result = tracker.closing_capture_once()
    assert result["captured"] == 1
    assert pick["closingPrice"] == -140
    # Closing implied above opening implied means the pick beat the close.
    assert pick["clvEdge"] > 0, "positive CLV is the tracker's primary KPI"
    assert pick["closingImplied"] > pick["openingImplied"]


def test_closing_capture_skips_picks_that_already_have_a_price(monkeypatch, tmp_path):
    priced = _pending_pick(closingPrice=-120)
    tracker, store, calls = _closing_env(monkeypatch, tmp_path, [priced], {})
    tracker.closing_capture_once()
    assert not calls, "a pick with a closing price must not trigger another buy"
