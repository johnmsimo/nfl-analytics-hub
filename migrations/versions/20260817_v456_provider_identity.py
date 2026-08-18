"""v4.5.6 cross-source player identity and weekly injury grain

Revision ID: 20260817_v456
Revises: 20260724_v443

Two importer defects need schema support:

* Snap counts identify players by Pro-Football-Reference id, which no column
  held, so every row was dropped. players.pfr_id stores the mapping that the
  roster feed already publishes alongside the gsis id.
* The nflverse injury feed has no report date at all. report_date was NOT NULL
  and part of the natural key, so every injury row failed the guard and was
  skipped. The feed's real grain is one report per player per team per week,
  so the column becomes nullable and drops out of the unique constraint.

SQLite cannot drop or alter a constraint in place, so both tables are rewritten
through batch_alter_table; Postgres executes the equivalent DDL directly.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "20260817_v456"
down_revision = "20260724_v443"
branch_labels = None
depends_on = None


def _players_has_pfr_id() -> bool:
    return any(c["name"] == "pfr_id" for c in inspect(op.get_bind()).get_columns("players"))


def _players_has_pfr_index() -> bool:
    return any(i["name"] == "ix_players_pfr_id" for i in inspect(op.get_bind()).get_indexes("players"))


def upgrade():
    # 20260811_base_schema builds `players` from live model metadata, so a fresh
    # database already carries pfr_id while an existing one still needs it.
    if not _players_has_pfr_id():
        with op.batch_alter_table("players") as batch:
            batch.add_column(sa.Column("pfr_id", sa.String(20), nullable=True))
    if not _players_has_pfr_index():
        op.create_index("ix_players_pfr_id", "players", ["pfr_id"], unique=True)

    with op.batch_alter_table("injury_reports") as batch:
        batch.alter_column("report_date", existing_type=sa.Date(), nullable=True)
        batch.drop_constraint("uq_injury_report", type_="unique")
        batch.create_unique_constraint(
            "uq_injury_report", ["player_id", "team_id", "season", "week"]
        )


def downgrade():
    with op.batch_alter_table("injury_reports") as batch:
        batch.drop_constraint("uq_injury_report", type_="unique")
        batch.create_unique_constraint(
            "uq_injury_report",
            ["player_id", "team_id", "season", "week", "report_date"],
        )
        batch.alter_column("report_date", existing_type=sa.Date(), nullable=False)

    if _players_has_pfr_index():
        op.drop_index("ix_players_pfr_id", table_name="players")
    if _players_has_pfr_id():
        with op.batch_alter_table("players") as batch:
            batch.drop_column("pfr_id")
