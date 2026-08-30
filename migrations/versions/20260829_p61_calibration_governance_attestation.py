"""P6.1 calibration governance audit attestations.

Revision ID: 20260829_p61_calibration_attest
Revises: 20260829_p54_game_market_cal
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "20260829_p61_calibration_attest"
down_revision = "20260829_p54_game_market_cal"
branch_labels = None
depends_on = None


def _table_exists(name: str) -> bool:
    return name in inspect(op.get_bind()).get_table_names()


def upgrade():
    if _table_exists("calibration_governance_attestations"):
        return
    op.create_table(
        "calibration_governance_attestations",
        sa.Column("attestation_id", sa.String(32), nullable=False),
        sa.Column("portfolio_digest", sa.String(64), nullable=False),
        sa.Column("event_count", sa.Integer(), nullable=False),
        sa.Column("audit_model_version", sa.String(64), nullable=False),
        sa.Column("audit_state", sa.String(32), nullable=False),
        sa.Column("champion_snapshot", sa.JSON(), nullable=False),
        sa.Column("integrity_snapshot", sa.JSON(), nullable=False),
        sa.Column("previous_attestation_digest", sa.String(64)),
        sa.Column("attestation_digest", sa.String(64), nullable=False),
        sa.Column("attested_by", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("attestation_id", name="pk_calibration_governance_attestations"),
        sa.UniqueConstraint("attestation_digest", name="uq_calibration_governance_attestation_digest"),
    )
    op.create_index(
        "ix_calibration_governance_attest_portfolio_digest",
        "calibration_governance_attestations",
        ["portfolio_digest"],
    )
    op.create_index(
        "ix_calibration_governance_attest_created_at",
        "calibration_governance_attestations",
        ["created_at"],
    )


def downgrade():
    if _table_exists("calibration_governance_attestations"):
        op.drop_table("calibration_governance_attestations")
