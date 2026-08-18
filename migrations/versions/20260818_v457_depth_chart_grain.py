"""v4.5.7 depth charts spanning both upstream grains

Revision ID: 20260818_v457
Revises: 20260817_v456

nflverse publishes depth charts in two shapes. Through 2024 they are official
weekly charts keyed by season and week with no date at all; from 2025 they are
dated snapshots keyed by `dt` with no week. chart_date was NOT NULL and carried
the natural key on its own, so every pre-2025 row failed the guard and the
dataset imported nothing for those seasons.

Make chart_date nullable and widen the key to cover both grains: weekly rows
identify by season and week, dated rows by chart_date. Postgres treats NULLs in
a unique constraint as distinct, so the constraint is a backstop rather than the
sole guarantee; the importer holds the natural keys it has already written and
does the exact deduplication.
"""

import sqlalchemy as sa
from alembic import op

revision = "20260818_v457"
down_revision = "20260817_v456"
branch_labels = None
depends_on = None

_NEW_KEY = ["player_id", "team_id", "season", "week", "chart_date", "depth_position"]
_OLD_KEY = ["player_id", "team_id", "chart_date", "depth_position"]


def upgrade():
    with op.batch_alter_table("depth_chart_entries") as batch:
        batch.alter_column("chart_date", existing_type=sa.Date(), nullable=True)
        batch.drop_constraint("uq_depth_chart_entry", type_="unique")
        batch.create_unique_constraint("uq_depth_chart_entry", _NEW_KEY)


def downgrade():
    # Rows with a null chart_date cannot satisfy the old key; drop them first.
    op.execute(sa.text("DELETE FROM depth_chart_entries WHERE chart_date IS NULL"))
    with op.batch_alter_table("depth_chart_entries") as batch:
        batch.drop_constraint("uq_depth_chart_entry", type_="unique")
        batch.create_unique_constraint("uq_depth_chart_entry", _OLD_KEY)
        batch.alter_column("chart_date", existing_type=sa.Date(), nullable=False)
