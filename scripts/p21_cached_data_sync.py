#!/usr/bin/env python3
"""Run the normal local-cache sync and print a sanitized P2.1 verification summary."""

from __future__ import annotations

import json
import os
from pathlib import Path

from data_ingestion import sync_cached_data


def _data_dir(app) -> Path:
    configured = (
        os.environ.get("NFL_SEED_DATA_DIR")
        or os.environ.get("SEED_DATA_DIR")
        or str(Path(app.root_path) / "data")
    )
    return Path(configured)


def main() -> int:
    from app import app

    with app.app_context():
        data_dir = _data_dir(app)
        result = sync_cached_data(data_dir)

    details = result.get("details") or {}
    sanitized = {
        "ok": result.get("status") == "completed",
        "source": "local-cache",
        "status": result.get("status"),
        "run_id": result.get("run_id"),
        "records_read": int(result.get("records_read") or 0),
        "records_written": int(result.get("records_written") or 0),
        "schedule_files": len(details.get("schedules") or []),
        "player_week_files": len(details.get("player_weeks") or []),
        "analytics_rebuilt": bool(details.get("analytics")),
    }
    print(json.dumps(sanitized, sort_keys=True))
    return 0 if sanitized["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
