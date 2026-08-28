"""P3.7 persistent Tracker and immutable decision ledger.

Revision ID: 20260828_p37_ledger
Revises: 20260828_p36_cache
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "20260828_p37_ledger"
down_revision = "20260828_p36_cache"
branch_labels = None
depends_on = None


def _table_exists(name: str) -> bool:
    return name in inspect(op.get_bind()).get_table_names()


def upgrade():
    if not _table_exists("tracker_day_snapshots"):
        op.create_table(
            "tracker_day_snapshots",
            sa.Column("event_date", sa.String(10), nullable=False),
            sa.Column("payload", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.PrimaryKeyConstraint("event_date", name="pk_tracker_day_snapshots"),
        )
        op.create_index(
            "ix_tracker_day_snapshots_updated_at",
            "tracker_day_snapshots",
            ["updated_at"],
        )

    if not _table_exists("tracker_settings_snapshots"):
        op.create_table(
            "tracker_settings_snapshots",
            sa.Column("settings_key", sa.String(40), nullable=False),
            sa.Column("payload", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.PrimaryKeyConstraint("settings_key", name="pk_tracker_settings_snapshots"),
        )

    if not _table_exists("decision_ledger_receipts"):
        op.create_table(
            "decision_ledger_receipts",
            sa.Column("receipt_id", sa.String(24), nullable=False),
            sa.Column("release_key", sa.String(320), nullable=False),
            sa.Column("event_date", sa.String(10)),
            sa.Column("season", sa.Integer()),
            sa.Column("week", sa.Integer()),
            sa.Column("season_type", sa.String(8)),
            sa.Column("game_id", sa.String(40), nullable=False),
            sa.Column("player_id", sa.String(80)),
            sa.Column("market_key", sa.String(80), nullable=False),
            sa.Column("decision_grade", sa.String(24), nullable=False),
            sa.Column("actionable", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("release_fingerprint", sa.String(64), nullable=False),
            sa.Column("release_payload", sa.JSON(), nullable=False),
            sa.Column("grade", sa.String(16), nullable=False, server_default="pending"),
            sa.Column("actual", sa.Float()),
            sa.Column("result_payload", sa.JSON()),
            sa.Column("released_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("graded_at", sa.DateTime(timezone=True)),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.PrimaryKeyConstraint("receipt_id", name="pk_decision_ledger_receipts"),
            sa.UniqueConstraint("release_key", name="uq_decision_ledger_release_key"),
        )
        for name, columns in (
            ("ix_decision_ledger_event_date", ["event_date"]),
            ("ix_decision_ledger_season_week", ["season", "week", "season_type"]),
            ("ix_decision_ledger_game", ["game_id"]),
            ("ix_decision_ledger_player", ["player_id"]),
            ("ix_decision_ledger_market", ["market_key"]),
            ("ix_decision_ledger_grade", ["grade"]),
            ("ix_decision_ledger_decision_grade", ["decision_grade"]),
            ("ix_decision_ledger_actionable", ["actionable"]),
            ("ix_decision_ledger_released_at", ["released_at"]),
            ("ix_decision_ledger_graded_at", ["graded_at"]),
        ):
            op.create_index(name, "decision_ledger_receipts", columns)


def downgrade():
    if _table_exists("decision_ledger_receipts"):
        op.drop_table("decision_ledger_receipts")
    if _table_exists("tracker_settings_snapshots"):
        op.drop_table("tracker_settings_snapshots")
    if _table_exists("tracker_day_snapshots"):
        op.drop_table("tracker_day_snapshots")
