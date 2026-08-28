"""Sanitized, read-only P3.5 production delivery verification."""
from __future__ import annotations

import json
import os


def main() -> int:
    from app import app
    from p35_verification import readiness_snapshot

    target = int(os.environ.get("P35_TARGET_SEASON", "2026"))
    with app.app_context():
        result = readiness_snapshot(target)
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
