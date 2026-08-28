"""Sanitized read-only P3.7 production verification."""
from __future__ import annotations

import json


def main() -> int:
    from app import app
    from p37_verification import readiness_snapshot

    with app.app_context():
        result = readiness_snapshot()
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
