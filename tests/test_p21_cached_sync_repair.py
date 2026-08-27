"""P2.1 cached-data repair workflow safety contracts."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_repair_workflow_requires_explicit_mutation_confirmation():
    workflow = (
        ROOT / ".github" / "workflows" / "p21-cached-data-sync-repair.yml"
    ).read_text(encoding="utf-8")

    assert "workflow_dispatch:" in workflow
    assert "RUN_CACHED_DATA_SYNC" in workflow
    assert "if: inputs.confirm_mutation == 'RUN_CACHED_DATA_SYNC'" in workflow
    assert "environment: production" in workflow
    assert "secrets.FLY_API_TOKEN" in workflow


def test_repair_workflow_uses_direct_fly_commands_and_no_paid_provider_secret():
    workflow = (
        ROOT / ".github" / "workflows" / "p21-cached-data-sync-repair.yml"
    ).read_text(encoding="utf-8")

    assert "/usr/bin/env PYTHONPATH=/app python /app/scripts/p21_cached_data_sync.py" in workflow
    assert "/usr/bin/env PYTHONPATH=/app python /app/scripts/p21_production_preview.py" in workflow
    assert "cd /app" not in workflow
    assert "ODDS_API_KEY" not in workflow
    assert "sync_external" not in workflow
    assert "sync_commercial" not in workflow


def test_cached_sync_runner_only_reports_sanitized_summary_fields():
    runner = (ROOT / "scripts" / "p21_cached_data_sync.py").read_text(encoding="utf-8")

    for field in (
        '"status"',
        '"run_id"',
        '"records_read"',
        '"records_written"',
        '"schedule_files"',
        '"player_week_files"',
        '"analytics_rebuilt"',
    ):
        assert field in runner
    assert "ODDS_API_KEY" not in runner
    assert "run.error" not in runner
