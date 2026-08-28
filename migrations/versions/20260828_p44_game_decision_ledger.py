"""P4.4 immutable game decision ledger.

Revision ID: 20260828_p44_game_ledger
Revises: 20260828_p37_ledger
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "20260828_p44_game_ledger"
down_revision = "20260828_p37_ledger"
branch_labels = None
depends_on = None


def _table_exists(name: str) -> bool:
    return name in inspect(op.get_bind()).get_table_names()


def upgrade():
    if _table_exists("game_decision_ledger_receipts"):
        return
    op.create_table(
        "game_decision_ledger_receipts",
        sa.Column("receipt_id", sa.String(24), nullable=False),
        sa.Column("release_key", sa.String(320), nullable=False),
        sa.Column("season", sa.Integer()),
        sa.Column("week", sa.Integer()),
        sa.Column("season_type", sa.String(8)),
        sa.Column("game_id", sa.String(40), nullable=False),
        sa.Column("market_key", sa.String(24), nullable=False),
        sa.Column("selected_side", sa.String(16), nullable=False),
        sa.Column("selected_team", sa.String(12)),
        sa.Column("decision_grade", sa.String(24), nullable=False),
        sa.Column("release_fingerprint", sa.String(64), nullable=False),
        sa.Column("release_payload", sa.JSON(), nullable=False),
        sa.Column("grade", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("home_score", sa.Integer()),
        sa.Column("away_score", sa.Integer()),
        sa.Column("result_payload", sa.JSON()),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("graded_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("receipt_id", name="pk_game_decision_ledger_receipts"),
        sa.UniqueConstraint("release_key", name="uq_game_decision_ledger_release_key"),
    )
    for name, columns in (
        ("ix_game_decision_ledger_season_week", ["season", "week", "season_type"]),
        ("ix_game_decision_ledger_game", ["game_id"]),
        ("ix_game_decision_ledger_market", ["market_key"]),
        ("ix_game_decision_ledger_grade", ["grade"]),
        ("ix_game_decision_ledger_decision_grade", ["decision_grade"]),
        ("ix_game_decision_ledger_released_at", ["released_at"]),
        ("ix_game_decision_ledger_graded_at", ["graded_at"]),
    ):
        op.create_index(name, "game_decision_ledger_receipts", columns)


def downgrade():
    if _table_exists("game_decision_ledger_receipts"):
        op.drop_table("game_decision_ledger_receipts")
