# P2.1 Production Preview Runbook

The manual P2.1 production preview is intentionally read-only. It must invoke the preview script through an actual executable because `flyctl ssh console --command` does not run shell built-ins or shell operators.

The supported command is:

```text
/usr/bin/env PYTHONPATH=/app python /app/scripts/p21_production_preview.py
```

Do not replace it with `cd`, `&&`, `export`, or other shell syntax unless the command is explicitly wrapped in a real shell executable.

The workflow still enforces the `RUN_READ_ONLY_PREVIEW` confirmation, checks production readiness, uses the existing Fly token, and performs no provider or Odds API request.
