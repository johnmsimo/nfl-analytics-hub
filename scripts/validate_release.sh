#!/usr/bin/env bash
# Local pre-release gate. This must stay runnable: every check below is one CI
# also enforces, so a green run here predicts a green pipeline.
#
# Ruff scope is the open question. `ruff check .` currently reports several
# hundred findings across the older modules, so a repo-wide gate can never pass
# and the script stops being run at all. The lint gate therefore matches the
# scope .github/workflows/quality.yml enforces, and STRICT=1 runs the whole repo
# for anyone working that backlog down.
set -euo pipefail

# Files linted and format-checked by CI (quality.yml).
RUFF_SCOPE=(
  database.py
  realtime_v32.py
  routes/v32_api.py
  routes/v32_release_api.py
  v32_release.py
)

python -m compileall -q analytics_engine routes tests

if [[ "${STRICT:-0}" == "1" ]]; then
  echo "==> ruff (STRICT: whole repository)"
  ruff check .
  ruff format --check .
else
  echo "==> ruff (CI scope; STRICT=1 for the whole repository)"
  ruff check "${RUFF_SCOPE[@]}"
  ruff format --check "${RUFF_SCOPE[@]}"
fi

mypy analytics_engine routes/analytics_api.py
pytest -q
bandit -q -r . -x tests,migrations,.venv
pip-audit -r requirements.txt
docker build -t nfl-analytics-hub:v3-candidate .
