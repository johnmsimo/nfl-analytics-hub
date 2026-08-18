"""v4.5.8 ESPN player identity

Revision ID: 20260818_v458
Revises: 20260818_v457

The warehouse takes player lines from two sources that name players
differently. The ESPN boxscore cache under data/ identifies athletes by ESPN
id; nflverse identifies them by gsis id. Each importer looked players up by its
own id in the same external_id column, so one person could own two Player rows
and their game lines were split across both — 2025 held 5,996 ESPN-keyed rows
beside 19,400 nflverse-keyed ones for the same season.

players.espn_id stores the mapping the roster feed already publishes next to
the gsis id, so the ESPN importer can resolve onto the player nflverse created
instead of minting a second row.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "20260818_v458"
down_revision = "20260818_v457"
branch_labels = None
depends_on = None


def _has_column() -> bool:
    return any(c["name"] == "espn_id" for c in inspect(op.get_bind()).get_columns("players"))


def _has_index() -> bool:
    return any(i["name"] == "ix_players_espn_id" for i in inspect(op.get_bind()).get_indexes("players"))


def upgrade():
    # 20260811_base_schema builds `players` from live model metadata, so a fresh
    # database already carries the column while an existing one still needs it.
    if not _has_column():
        with op.batch_alter_table("players") as batch:
            batch.add_column(sa.Column("espn_id", sa.String(20), nullable=True))
    if not _has_index():
        op.create_index("ix_players_espn_id", "players", ["espn_id"], unique=True)


def downgrade():
    if _has_index():
        op.drop_index("ix_players_espn_id", table_name="players")
    if _has_column():
        with op.batch_alter_table("players") as batch:
            batch.drop_column("espn_id")
