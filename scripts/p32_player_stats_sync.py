#!/usr/bin/env python3
"""Protected P3.2 production player-stat population entrypoint."""
from __future__ import annotations

import json
import os

from app import app
from player_stats_warehouse import populate_player_stats


def main() -> int:
    target = int(os.environ.get("P32_TARGET_SEASON", "2026"))
    baseline = int(os.environ.get("P32_BASELINE_SEASON", str(target - 1)))
    with app.app_context():
        result = populate_player_stats(target, baseline)
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
