"""P3.6 durable provider cache snapshots.

Revision ID: 20260828_p36_cache
Revises: 20260825_p21
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "20260828_p36_cache"
down_revision = "20260825_p21"
branch_labels = None
depends_on = None


def _table_exists(name: str) -> bool:
    return name in inspect(op.get_bind()).get_table_names()


def upgrade():
    if _table_exists("provider_cache_snapshots"):
        return
    op.create_table(
        "provider_cache_snapshots",
        sa.Column("provider_key", sa.String(80), nullable=False),
        sa.Column("cache_key", sa.String(180), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("provider_key", "cache_key", name="pk_provider_cache_snapshots"),
    )
    op.create_index(
        "ix_provider_cache_snapshots_updated_at",
        "provider_cache_snapshots",
        ["updated_at"],
    )


def downgrade():
    if _table_exists("provider_cache_snapshots"):
        op.drop_table("provider_cache_snapshots")
