#!/usr/bin/env python3
"""Populate and verify the 2026 production player warehouse for P3.1."""

from __future__ import annotations

import json
import os

from player_warehouse import populate_player_warehouse


def main() -> int:
    from app import app

    season = int(os.environ.get("P31_SEASON", "2026"))
    if season != 2026:
        raise SystemExit("P3.1 production sync is restricted to season 2026")

    with app.app_context():
        result = populate_player_warehouse(season)

    sanitized = {
        "ok": bool(result.get("ok")),
        "phase": "P3.1",
        "mode": "player-warehouse-sync",
        "season": season,
        "provider": result.get("provider"),
        "dataset": result.get("dataset"),
        "sync": result.get("sync"),
        "normalization": result.get("normalization"),
        "warehouse": result.get("warehouse"),
    }
    print(json.dumps(sanitized, sort_keys=True))
    return 0 if sanitized["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
