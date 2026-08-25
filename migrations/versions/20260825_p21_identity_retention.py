"""P2.1 source-scoped player identity and warehouse retention indexes.

Revision ID: 20260825_p21
Revises: 20260818_v458
"""

from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "20260825_p21"
down_revision = "20260818_v458"
branch_labels = None
depends_on = None


def _table_exists(name: str) -> bool:
    return name in inspect(op.get_bind()).get_table_names()


def _index_exists(table: str, name: str) -> bool:
    return any(index["name"] == name for index in inspect(op.get_bind()).get_indexes(table))


def _backfill_player_identities() -> None:
    bind = op.get_bind()
    players = sa.table(
        "players",
        sa.column("id", sa.Integer),
        sa.column("external_id", sa.String),
        sa.column("pfr_id", sa.String),
        sa.column("espn_id", sa.String),
    )
    identities = sa.table(
        "player_external_identities",
        sa.column("player_id", sa.Integer),
        sa.column("source_key", sa.String),
        sa.column("external_id", sa.String),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    existing = {
        (row.source_key, row.external_id)
        for row in bind.execute(
            sa.select(identities.c.source_key, identities.c.external_id)
        )
    }
    transaction_players = set()
    if _table_exists("league_transactions"):
        transaction_players = {
            row[0]
            for row in bind.execute(
                sa.text(
                    "SELECT DISTINCT player_id FROM league_transactions "
                    "WHERE player_id IS NOT NULL"
                )
            )
        }

    now = datetime.now(timezone.utc)
    inserts = []
    for row in bind.execute(sa.select(players)).mappings():
        candidates = []
        if row.get("pfr_id"):
            candidates.append(("pfr", str(row["pfr_id"])))
        if row.get("espn_id"):
            candidates.append(("espn", str(row["espn_id"])))
        external_id = str(row.get("external_id") or "").strip()
        if external_id:
            if external_id.startswith("00-"):
                source = "nflverse"
            elif external_id.isdigit() and not row.get("espn_id"):
                source = "sportsdataio" if row["id"] in transaction_players else "espn"
            else:
                source = "legacy"
            candidates.append((source, external_id))
        for source, provider_id in candidates:
            key = (source, provider_id)
            if key in existing:
                continue
            existing.add(key)
            inserts.append({
                "player_id": row["id"],
                "source_key": source,
                "external_id": provider_id,
                "created_at": now,
                "updated_at": now,
            })
    if inserts:
        op.bulk_insert(identities, inserts)


def upgrade():
    if not _table_exists("player_external_identities"):
        op.create_table(
            "player_external_identities",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column(
                "player_id",
                sa.Integer(),
                sa.ForeignKey("players.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("source_key", sa.String(40), nullable=False),
            sa.Column("external_id", sa.String(80), nullable=False),
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
            sa.UniqueConstraint(
                "source_key",
                "external_id",
                name="uq_player_external_identity",
            ),
        )
    if not _index_exists("player_external_identities", "ix_player_external_identities_player_id"):
        op.create_index(
            "ix_player_external_identities_player_id",
            "player_external_identities",
            ["player_id"],
        )
    if not _index_exists("player_external_identities", "ix_player_identity_player_source"):
        op.create_index(
            "ix_player_identity_player_source",
            "player_external_identities",
            ["player_id", "source_key"],
        )
    if not _index_exists("raw_ingest_records", "ix_raw_ingest_retention"):
        op.create_index(
            "ix_raw_ingest_retention",
            "raw_ingest_records",
            ["source_id", "entity_type", "external_id", "ingested_at"],
        )
    _backfill_player_identities()


def downgrade():
    if _index_exists("raw_ingest_records", "ix_raw_ingest_retention"):
        op.drop_index("ix_raw_ingest_retention", table_name="raw_ingest_records")
    if _table_exists("player_external_identities"):
        op.drop_table("player_external_identities")
