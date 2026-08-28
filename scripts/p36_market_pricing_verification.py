"""Sanitized P3.6 production verification entrypoint."""
from __future__ import annotations

import json
import os


def main() -> int:
    from app import app
    from p36_verification import readiness_snapshot

    target = int(os.environ.get("P36_TARGET_SEASON", "2026"))
    mode = os.environ.get("P36_REFRESH_MODE", "cache")
    with app.app_context():
        result = readiness_snapshot(target, refresh_mode=mode)
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
