"""v4.4.1 persistent enterprise identity and API keys

Revision ID: 20260723_v441
Revises: 20260718_phase12
"""

import sqlalchemy as sa
from alembic import op

revision = "20260723_v441"
down_revision = "20260718_phase12"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "enterprise_organizations",
        sa.Column("organization_id", sa.String(24), primary_key=True),
        sa.Column("slug", sa.String(64), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("data_region", sa.String(80)),
        sa.Column("tags", sa.JSON(), nullable=False),
        sa.Column("created_by_type", sa.String(16), nullable=False),
        sa.Column("created_by_id", sa.String(160), nullable=False),
        sa.Column("metadata_digest", sa.String(71), nullable=False),
        sa.Column("contract_version", sa.String(16), nullable=False),
        sa.Column("contract_created_at", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('active', 'suspended', 'archived')",
            name="ck_enterprise_organization_status",
        ),
        sa.CheckConstraint(
            "created_by_type IN ('user', 'service')",
            name="ck_enterprise_organization_creator_type",
        ),
        sa.UniqueConstraint("slug", name="uq_enterprise_organization_slug"),
    )
    op.create_index(
        "ix_enterprise_organizations_slug",
        "enterprise_organizations",
        ["slug"],
        unique=True,
    )
    op.create_index(
        "ix_enterprise_organizations_status",
        "enterprise_organizations",
        ["status"],
    )

    op.create_table(
        "enterprise_memberships",
        sa.Column("membership_id", sa.String(31), primary_key=True),
        sa.Column("organization_id", sa.String(24), nullable=False),
        sa.Column("subject_type", sa.String(16), nullable=False),
        sa.Column("subject_id", sa.String(160), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("permissions", sa.JSON(), nullable=False),
        sa.Column("granted_by_type", sa.String(16), nullable=False),
        sa.Column("granted_by_id", sa.String(160), nullable=False),
        sa.Column("metadata_digest", sa.String(71), nullable=False),
        sa.Column("contract_version", sa.String(16), nullable=False),
        sa.Column("contract_granted_at", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "subject_type IN ('user', 'service')",
            name="ck_enterprise_membership_subject_type",
        ),
        sa.CheckConstraint(
            "role IN ('owner', 'admin', 'analyst', 'viewer')",
            name="ck_enterprise_membership_role",
        ),
        sa.CheckConstraint(
            "status IN ('invited', 'active', 'suspended', 'removed')",
            name="ck_enterprise_membership_status",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["enterprise_organizations.organization_id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "organization_id",
            "subject_type",
            "subject_id",
            name="uq_enterprise_membership_subject",
        ),
    )
    op.create_index(
        "ix_enterprise_memberships_organization_id",
        "enterprise_memberships",
        ["organization_id"],
    )
    op.create_index(
        "ix_enterprise_memberships_role",
        "enterprise_memberships",
        ["role"],
    )
    op.create_index(
        "ix_enterprise_memberships_status",
        "enterprise_memberships",
        ["status"],
    )

    op.create_table(
        "enterprise_api_keys",
        sa.Column("api_key_id", sa.String(27), primary_key=True),
        sa.Column("organization_id", sa.String(24), nullable=False),
        sa.Column("membership_id", sa.String(31), nullable=False),
        sa.Column("subject_type", sa.String(16), nullable=False),
        sa.Column("subject_id", sa.String(160), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("prefix", sa.String(16), nullable=False),
        sa.Column("secret_digest", sa.String(64), nullable=False),
        sa.Column("scopes", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("issued_by_type", sa.String(16), nullable=False),
        sa.Column("issued_by_id", sa.String(160), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("rotated_from_id", sa.String(27)),
        sa.CheckConstraint(
            "status IN ('active', 'revoked')",
            name="ck_enterprise_api_key_status",
        ),
        sa.CheckConstraint(
            "subject_type IN ('user', 'service')",
            name="ck_enterprise_api_key_subject_type",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["enterprise_organizations.organization_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["membership_id"],
            ["enterprise_memberships.membership_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["rotated_from_id"],
            ["enterprise_api_keys.api_key_id"],
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint("prefix", name="uq_enterprise_api_key_prefix"),
    )
    op.create_index(
        "ix_enterprise_api_keys_organization_id",
        "enterprise_api_keys",
        ["organization_id"],
    )
    op.create_index(
        "ix_enterprise_api_keys_membership_id",
        "enterprise_api_keys",
        ["membership_id"],
    )
    op.create_index(
        "ix_enterprise_api_keys_prefix",
        "enterprise_api_keys",
        ["prefix"],
        unique=True,
    )
    op.create_index(
        "ix_enterprise_api_keys_status",
        "enterprise_api_keys",
        ["status"],
    )
    op.create_index(
        "ix_enterprise_api_keys_expires_at",
        "enterprise_api_keys",
        ["expires_at"],
    )


def downgrade():
    op.drop_table("enterprise_api_keys")
    op.drop_table("enterprise_memberships")
    op.drop_table("enterprise_organizations")
