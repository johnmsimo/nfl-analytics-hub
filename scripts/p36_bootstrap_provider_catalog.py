"""Explicit P3.6 provider-catalog bootstrap for an empty durable Odds cache.

This script is only called by the protected one-event refresh workflow. It may
make one Odds API game-catalog request when the durable cache contains no game
events. Cache-only verification never invokes it.
"""
from __future__ import annotations

import json

import odds_api
from app import app


def main() -> int:
    with app.app_context():
        before = odds_api.snapshot_status()
        before_events = int(before.get("game_events") or 0)
        if before_events > 0:
            print(
                json.dumps(
                    {
                        "ok": True,
                        "catalogFetchPerformed": False,
                        "gameEventsBefore": before_events,
                        "gameEventsAfter": before_events,
                        "cachePersistence": before.get("cache_persistence"),
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                )
            )
            return 0

        events = odds_api.get_game_odds(force=True)
        after = odds_api.snapshot_status()
        after_events = int(after.get("game_events") or 0)
        payload = {
            "ok": bool(events) and after_events > 0,
            "catalogFetchPerformed": True,
            "gameEventsBefore": before_events,
            "gameEventsAfter": after_events,
            "cachePersistence": after.get("cache_persistence"),
        }
        print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
        return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
