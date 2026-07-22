#!/usr/bin/env bash
set -euo pipefail
python -m compileall -q analytics_engine routes tests
ruff check .
ruff format --check .
mypy analytics_engine routes/analytics_api.py
pytest -q
bandit -q -r . -x tests,migrations,.venv
pip-audit -r requirements.txt
docker build -t nfl-analytics-hub:v3-candidate .
