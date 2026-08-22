#!/usr/bin/env python3
"""Validate a season schedule snapshot before it can be deployed."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime
from pathlib import Path


EXPECTED_COUNTS = {"PRE": 49, "REG": 272, "POST": 13}
EXPECTED_PRE_WEEKS = {0: 1, 1: 16, 2: 16, 3: 16}
REQUIRED_FIELDS = {
    "game_id",
    "season",
    "season_type",
    "week",
    "date",
    "home_team",
    "away_team",
}


def validate(path: Path) -> list[str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    games = payload.get("games")
    if not isinstance(games, list):
        return ["top-level games must be a list"]

    errors: list[str] = []
    ids: list[str] = []
    for index, game in enumerate(games):
        missing = sorted(field for field in REQUIRED_FIELDS if game.get(field) in (None, ""))
        if missing:
            errors.append(f"game {index} is missing: {', '.join(missing)}")
            continue

        game_id = str(game["game_id"])
        ids.append(game_id)
        if game["season"] != 2026:
            errors.append(f"game {game_id} has season {game['season']}, expected 2026")
        if game["season_type"] not in EXPECTED_COUNTS:
            errors.append(f"game {game_id} has invalid season_type {game['season_type']}")
        if game["home_team"] == game["away_team"] and game["home_team"] != "TBD":
            errors.append(f"game {game_id} has the same home and away team")
        try:
            datetime.fromisoformat(str(game["date"]).replace("Z", "+00:00"))
        except ValueError:
            errors.append(f"game {game_id} has invalid ISO date {game['date']}")

    duplicates = sorted(game_id for game_id, count in Counter(ids).items() if count > 1)
    if duplicates:
        errors.append(f"duplicate game ids: {', '.join(duplicates)}")

    counts = Counter(game.get("season_type") for game in games)
    for season_type, expected in EXPECTED_COUNTS.items():
        if counts[season_type] != expected:
            errors.append(
                f"{season_type} count is {counts[season_type]}, expected {expected}"
            )

    preseason_weeks = Counter(
        game.get("week") for game in games if game.get("season_type") == "PRE"
    )
    if dict(preseason_weeks) != EXPECTED_PRE_WEEKS:
        errors.append(
            f"preseason week distribution is {dict(preseason_weeks)}, "
            f"expected {EXPECTED_PRE_WEEKS}"
        )

    expected_order = sorted(
        games,
        key=lambda game: (game.get("date") or "", str(game.get("game_id") or "")),
    )
    if games != expected_order:
        errors.append("games are not sorted by date and game id")

    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    args = parser.parse_args()

    errors = validate(args.path)
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1

    print(f"Validated {args.path}: 334 games (49 PRE, 272 REG, 13 POST)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
