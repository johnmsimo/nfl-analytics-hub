"""P5.4 spread/total calibration promotion history.

Revision ID: 20260829_p54_game_market_cal
Revises: 20260829_p50_game_calibration
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "20260829_p54_game_market_cal"
down_revision = "20260829_p50_game_calibration"
branch_labels = None
depends_on = None


def _table_exists(name: str) -> bool:
    return name in inspect(op.get_bind()).get_table_names()


def upgrade():
    if _table_exists("game_market_calibration_promotion_events"):
        return
    op.create_table(
        "game_market_calibration_promotion_events",
        sa.Column("event_id", sa.String(32), nullable=False),
        sa.Column("market_key", sa.String(24), nullable=False),
        sa.Column("action", sa.String(16), nullable=False),
        sa.Column("candidate_id", sa.String(64)),
        sa.Column("family", sa.String(32)),
        sa.Column("slope", sa.Float()),
        sa.Column("intercept", sa.Float()),
        sa.Column("base_model_version", sa.String(64), nullable=False),
        sa.Column("approved_by", sa.String(128), nullable=False),
        sa.Column("governance_fingerprint", sa.String(64), nullable=False),
        sa.Column("governance_payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("event_id", name="pk_game_market_calibration_promotion_events"),
    )
    op.create_index(
        "ix_game_market_calibration_market",
        "game_market_calibration_promotion_events",
        ["market_key"],
    )
    op.create_index(
        "ix_game_market_calibration_action",
        "game_market_calibration_promotion_events",
        ["action"],
    )
    op.create_index(
        "ix_game_market_calibration_candidate",
        "game_market_calibration_promotion_events",
        ["candidate_id"],
    )
    op.create_index(
        "ix_game_market_calibration_created_at",
        "game_market_calibration_promotion_events",
        ["created_at"],
    )


def downgrade():
    if _table_exists("game_market_calibration_promotion_events"):
        op.drop_table("game_market_calibration_promotion_events")
