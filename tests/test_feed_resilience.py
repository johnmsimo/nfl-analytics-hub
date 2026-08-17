"""A provider blip must not be memoized for the success TTL."""

import time

import nfl_data


def _clear(key):
    with nfl_data._lock:
        nfl_data._mem.pop(key, None)


def _expiry(key):
    with nfl_data._lock:
        return nfl_data._mem[key][0]


def test_failed_injuries_fetch_expires_quickly(monkeypatch):
    _clear("injuries")

    def boom(*a, **k):
        raise RuntimeError("ESPN fetch failed")

    monkeypatch.setattr(nfl_data, "_get_json", boom)
    assert nfl_data.get_injuries(refresh=True) == []
    ttl = _expiry("injuries") - time.time()
    assert ttl <= nfl_data.FAILURE_TTL + 1, f"failure cached for {ttl:.0f}s"
    assert ttl < 3600, "a failed fetch must not hold the success TTL"


def test_failed_news_fetch_expires_quickly(monkeypatch):
    key = "news:3"
    _clear(key)

    def boom(*a, **k):
        raise RuntimeError("ESPN fetch failed")

    monkeypatch.setattr(nfl_data, "_get_json", boom)
    assert nfl_data.get_news(3) == []
    ttl = _expiry(key) - time.time()
    assert ttl <= nfl_data.FAILURE_TTL + 1, f"failure cached for {ttl:.0f}s"
    assert ttl < 900, "a failed fetch must not hold the success TTL"


def test_successful_news_fetch_keeps_the_long_ttl(monkeypatch):
    key = "news:2"
    _clear(key)
    article = {"headline": "h", "description": "d", "published": "p", "links": {"web": {"href": "u"}}}
    monkeypatch.setattr(nfl_data, "_get_json", lambda *a, **k: {"articles": [article, article]})
    assert len(nfl_data.get_news(2)) == 2
    assert _expiry(key) - time.time() > nfl_data.FAILURE_TTL + 1


def test_recovery_is_served_once_the_failure_entry_lapses(monkeypatch):
    """After the short window the next call refetches rather than serving empty."""
    _clear("injuries")
    monkeypatch.setattr(nfl_data, "_get_json", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))
    assert nfl_data.get_injuries(refresh=True) == []

    # Expire the negative entry the way the clock would.
    with nfl_data._lock:
        nfl_data._mem["injuries"] = (time.time() - 1, [])

    monkeypatch.setattr(nfl_data, "_team_abbrev_map", lambda: {"1": "ATL"})
    monkeypatch.setattr(
        nfl_data,
        "_get_json",
        lambda *a, **k: {
            "injuries": [{"id": "1", "injuries": [{"status": "Out", "athlete": {"displayName": "P"}}]}]
        },
    )
    assert len(nfl_data.get_injuries()) == 1
