"""P2.1 production-preview safety contracts."""

import json
from pathlib import Path

from database import db
from db_models import DataSyncRun
from scripts.p21_production_preview import _error_category, build_preview

ROOT = Path(__file__).resolve().parents[1]


def test_preview_is_read_only_and_does_not_expose_sync_error(app_fixture):
    secret_marker = "postgresql://admin:do-not-print@private-db/internal"
    with app_fixture.app_context():
        run = DataSyncRun(
            source="local-cache",
            status="failed",
            error=f"IntegrityError duplicate key while opening {secret_marker}",
        )
        db.session.add(run)
        db.session.commit()

        result = build_preview()

        assert result["ok"] is True
        assert result["mode"] == "read-only"
        assert result["identity_reconciliation"]["dry_run"] is True
        assert result["identity_reconciliation"]["players_merged"] == 0
        assert result["identity_reconciliation"]["identity_links_added"] == 0
        assert result["warehouse_retention"]["dry_run"] is True
        assert all(value == 0 for value in result["warehouse_retention"]["deleted"].values())
        assert result["latest_cached_data_sync"]["error_category"] == "database_integrity"
        assert secret_marker not in json.dumps(result)
        db.session.delete(run)
        db.session.commit()


def test_sync_error_categories_are_sanitized():
    assert _error_category(None) is None
    assert _error_category("no such table: player_external_identities") == "schema_missing_table"
    assert _error_category("UndefinedColumn: missing") == "schema_missing_column"
    assert _error_category("connection refused") == "database_operational"
    assert _error_category("JSONDecodeError") == "cache_input"
    assert _error_category("unexpected provider response") == "unclassified"


def test_workflow_is_manual_read_only_and_uses_existing_fly_secret():
    workflow = (ROOT / ".github" / "workflows" / "p21-production-preview.yml").read_text(encoding="utf-8")

    assert "workflow_dispatch:" in workflow
    assert "RUN_READ_ONLY_PREVIEW" in workflow
    assert "environment: production" in workflow
    assert "secrets.FLY_API_TOKEN" in workflow
    assert "python scripts/p21_production_preview.py" in workflow
    assert "warehouse-retention/apply" not in workflow
    assert "player-identities/reconcile" not in workflow
    assert "ODDS_API_KEY" not in workflow
