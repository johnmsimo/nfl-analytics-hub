"""Authoritative pre-v10 schema for fresh production databases.

Revision ID: 20260811_base_schema
Revises: 20260718_phase10

The phase 10 migration historically served only as a marker.  This migration
creates the model tables that existed before phase 10 so later migrations can
legitimately add foreign keys to them on a fresh database.
"""

from alembic import op

revision = "20260811_base_schema"
down_revision = "20260718_phase10"
branch_labels = None
depends_on = None


# Tables created by later migrations are intentionally excluded.  Their
# migrations remain responsible for their own DDL and indexes.
BASE_TABLE_NAMES = (
    "teams",
    "seasons",
    "games",
    "team_game_stats",
    "players",
    "player_team_seasons",
    "player_game_stats",
    "coaches",
    "coaching_assignments",
    "analytics_snapshots",
    "data_sync_runs",
    "team_season_stats",
    "player_season_stats",
    "coach_season_stats",
    "data_sources",
    "raw_ingest_records",
    "data_quality_issues",
    "schema_versions",
    "plays",
    "drives",
    "team_advanced_season_stats",
    "model_versions",
    "predictions",
    "scheduled_jobs",
    "audit_logs",
)


def _base_tables():
    # Importing the application metadata here keeps the migration aligned with
    # the canonical model definitions while avoiding model imports at module
    # collection time.
    from database import db
    import db_models  # noqa: F401

    missing = [name for name in BASE_TABLE_NAMES if name not in db.metadata.tables]
    if missing:
        raise RuntimeError(f"Base schema metadata is missing tables: {', '.join(missing)}")
    return [db.metadata.tables[name] for name in BASE_TABLE_NAMES]


def upgrade():
    bind = op.get_bind()
    from database import db

    # db_models is imported by _base_tables and registers every model table.
    db.metadata.create_all(bind=bind, tables=_base_tables(), checkfirst=True)


def downgrade():
    bind = op.get_bind()
    for table in reversed(_base_tables()):
        table.drop(bind=bind, checkfirst=True)
