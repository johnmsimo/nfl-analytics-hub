"""v4.4.3 shared workspaces and enterprise operations

Revision ID: 20260724_v443
Revises: 20260723_v441
"""

import sqlalchemy as sa
from alembic import op

revision = "20260724_v443"
down_revision = "20260723_v441"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "enterprise_workspaces",
        sa.Column("workspace_id", sa.String(30), primary_key=True),
        sa.Column("organization_id", sa.String(24), nullable=False),
        sa.Column("slug", sa.String(64), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("description", sa.String(1000)),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("created_by_membership_id", sa.String(31), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('active', 'archived')",
            name="ck_enterprise_workspace_status",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["enterprise_organizations.organization_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_membership_id"],
            ["enterprise_memberships.membership_id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "organization_id",
            "slug",
            name="uq_enterprise_workspace_slug",
        ),
    )
    op.create_index(
        "ix_enterprise_workspaces_organization_id",
        "enterprise_workspaces",
        ["organization_id"],
    )
    op.create_index(
        "ix_enterprise_workspaces_status",
        "enterprise_workspaces",
        ["status"],
    )
    op.create_index(
        "ix_enterprise_workspaces_created_by_membership_id",
        "enterprise_workspaces",
        ["created_by_membership_id"],
    )

    op.create_table(
        "enterprise_workspace_collaborators",
        sa.Column("collaborator_id", sa.String(33), primary_key=True),
        sa.Column("organization_id", sa.String(24), nullable=False),
        sa.Column("workspace_id", sa.String(30), nullable=False),
        sa.Column("membership_id", sa.String(31), nullable=False),
        sa.Column("access_level", sa.String(16), nullable=False),
        sa.Column("granted_by_membership_id", sa.String(31), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "access_level IN ('viewer', 'editor', 'manager')",
            name="ck_enterprise_workspace_access_level",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["enterprise_organizations.organization_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["enterprise_workspaces.workspace_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["membership_id"],
            ["enterprise_memberships.membership_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["granted_by_membership_id"],
            ["enterprise_memberships.membership_id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "membership_id",
            name="uq_enterprise_workspace_collaborator",
        ),
    )
    op.create_index(
        "ix_enterprise_workspace_collaborators_organization_id",
        "enterprise_workspace_collaborators",
        ["organization_id"],
    )
    op.create_index(
        "ix_enterprise_workspace_collaborators_workspace_id",
        "enterprise_workspace_collaborators",
        ["workspace_id"],
    )
    op.create_index(
        "ix_enterprise_workspace_collaborators_membership_id",
        "enterprise_workspace_collaborators",
        ["membership_id"],
    )
    op.create_index(
        "ix_enterprise_workspace_collaborators_access_level",
        "enterprise_workspace_collaborators",
        ["access_level"],
    )

    op.create_table(
        "enterprise_saved_decisions",
        sa.Column("decision_id", sa.String(29), primary_key=True),
        sa.Column("organization_id", sa.String(24), nullable=False),
        sa.Column("workspace_id", sa.String(30), nullable=False),
        sa.Column("operation", sa.String(80), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("payload", sa.JSON()),
        sa.Column("payload_digest", sa.String(71), nullable=False),
        sa.Column("tags", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("created_by_membership_id", sa.String(31), nullable=False),
        sa.Column("retained_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expired_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('active', 'expired')",
            name="ck_enterprise_saved_decision_status",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["enterprise_organizations.organization_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["enterprise_workspaces.workspace_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_membership_id"],
            ["enterprise_memberships.membership_id"],
            ondelete="RESTRICT",
        ),
    )
    op.create_index(
        "ix_enterprise_saved_decisions_organization_id",
        "enterprise_saved_decisions",
        ["organization_id"],
    )
    op.create_index(
        "ix_enterprise_saved_decisions_workspace_id",
        "enterprise_saved_decisions",
        ["workspace_id"],
    )
    op.create_index(
        "ix_enterprise_saved_decisions_operation",
        "enterprise_saved_decisions",
        ["operation"],
    )
    op.create_index(
        "ix_enterprise_saved_decisions_status",
        "enterprise_saved_decisions",
        ["status"],
    )
    op.create_index(
        "ix_enterprise_saved_decisions_retained_until",
        "enterprise_saved_decisions",
        ["retained_until"],
    )

    op.create_table(
        "enterprise_reports",
        sa.Column("report_id", sa.String(27), primary_key=True),
        sa.Column("organization_id", sa.String(24), nullable=False),
        sa.Column("workspace_id", sa.String(30), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("content", sa.JSON()),
        sa.Column("content_digest", sa.String(71), nullable=False),
        sa.Column("decision_ids", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("created_by_membership_id", sa.String(31), nullable=False),
        sa.Column("retained_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expired_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('draft', 'published', 'archived', 'expired')",
            name="ck_enterprise_report_status",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["enterprise_organizations.organization_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["enterprise_workspaces.workspace_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_membership_id"],
            ["enterprise_memberships.membership_id"],
            ondelete="RESTRICT",
        ),
    )
    op.create_index(
        "ix_enterprise_reports_organization_id",
        "enterprise_reports",
        ["organization_id"],
    )
    op.create_index(
        "ix_enterprise_reports_workspace_id",
        "enterprise_reports",
        ["workspace_id"],
    )
    op.create_index(
        "ix_enterprise_reports_status",
        "enterprise_reports",
        ["status"],
    )
    op.create_index(
        "ix_enterprise_reports_retained_until",
        "enterprise_reports",
        ["retained_until"],
    )

    op.create_table(
        "enterprise_retention_policies",
        sa.Column("organization_id", sa.String(24), primary_key=True),
        sa.Column("decision_days", sa.Integer(), nullable=False),
        sa.Column("report_days", sa.Integer(), nullable=False),
        sa.Column("export_enabled", sa.Boolean(), nullable=False),
        sa.Column("updated_by_membership_id", sa.String(31), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "decision_days BETWEEN 1 AND 3650",
            name="ck_enterprise_retention_decision_days",
        ),
        sa.CheckConstraint(
            "report_days BETWEEN 1 AND 3650",
            name="ck_enterprise_retention_report_days",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["enterprise_organizations.organization_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_membership_id"],
            ["enterprise_memberships.membership_id"],
            ondelete="RESTRICT",
        ),
    )

    op.create_table(
        "enterprise_audit_events",
        sa.Column("sequence", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("event_id", sa.String(27), nullable=False),
        sa.Column("organization_id", sa.String(24), nullable=False),
        sa.Column("workspace_id", sa.String(30)),
        sa.Column("actor_membership_id", sa.String(31), nullable=False),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("resource_type", sa.String(40), nullable=False),
        sa.Column("resource_id", sa.String(80), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("previous_digest", sa.String(71)),
        sa.Column("event_digest", sa.String(71), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["enterprise_organizations.organization_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["enterprise_workspaces.workspace_id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["actor_membership_id"],
            ["enterprise_memberships.membership_id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("event_id", name="uq_enterprise_audit_event_id"),
        sa.UniqueConstraint("event_digest", name="uq_enterprise_audit_event_digest"),
    )
    op.create_index(
        "ix_enterprise_audit_events_event_id",
        "enterprise_audit_events",
        ["event_id"],
        unique=True,
    )
    op.create_index(
        "ix_enterprise_audit_events_organization_id",
        "enterprise_audit_events",
        ["organization_id"],
    )
    op.create_index(
        "ix_enterprise_audit_events_workspace_id",
        "enterprise_audit_events",
        ["workspace_id"],
    )
    op.create_index(
        "ix_enterprise_audit_events_actor_membership_id",
        "enterprise_audit_events",
        ["actor_membership_id"],
    )
    op.create_index(
        "ix_enterprise_audit_events_action",
        "enterprise_audit_events",
        ["action"],
    )
    op.create_index(
        "ix_enterprise_audit_events_resource_type",
        "enterprise_audit_events",
        ["resource_type"],
    )
    op.create_index(
        "ix_enterprise_audit_events_resource_id",
        "enterprise_audit_events",
        ["resource_id"],
    )
    op.create_index(
        "ix_enterprise_audit_events_occurred_at",
        "enterprise_audit_events",
        ["occurred_at"],
    )


def downgrade():
    op.drop_table("enterprise_audit_events")
    op.drop_table("enterprise_retention_policies")
    op.drop_table("enterprise_reports")
    op.drop_table("enterprise_saved_decisions")
    op.drop_table("enterprise_workspace_collaborators")
    op.drop_table("enterprise_workspaces")
