"""P2.5 regression tests for repository quality policy."""

from __future__ import annotations

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_repository_coverage_floor_is_at_least_68_percent():
    with (ROOT / "pyproject.toml").open("rb") as handle:
        config = tomllib.load(handle)
    report = config["tool"]["coverage"]["report"]
    assert float(report["fail_under"]) >= 68
    assert int(report["precision"]) >= 2
    assert report["show_missing"] is True


def test_ci_uses_centralized_repository_coverage_policy():
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "pytest -q --cov=. --cov-report=term-missing" in workflow
    assert "--cov-fail-under=60" not in workflow


def test_quality_workflow_has_repository_wide_correctness_lint():
    workflow = (ROOT / ".github" / "workflows" / "quality.yml").read_text(encoding="utf-8")
    assert "ruff check . --select E9,F63,F7,F82" in workflow
    assert "api_lifecycle.py" in workflow
    assert "routes/current_api.py" in workflow


def test_analytics_coverage_floor_is_at_least_90_percent():
    workflow = (ROOT / ".github" / "workflows" / "quality.yml").read_text(encoding="utf-8")
    assert "--cov-fail-under=90" in workflow
