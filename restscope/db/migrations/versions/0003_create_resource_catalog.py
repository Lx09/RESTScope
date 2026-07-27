"""create resource catalog tables

Revision ID: 0003_create_resource_catalog
Revises: 0002_create_generator_configs
Create Date: 2026-07-23
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0003_create_resource_catalog"
down_revision = "0002_create_generator_configs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """
    Handle upgrade as part of the repository and database persistence boundary.

    The annotated arguments and return type define the data boundary used by callers.
    """
    op.create_table(
        "resources",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("canonical_name", sa.Text(), nullable=False),
        sa.Column("normalized_name", sa.Text(), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "resource_aliases",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "resource_id",
            sa.String(),
            sa.ForeignKey("resources.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("alias", sa.Text(), nullable=False),
        sa.Column("normalized_alias", sa.Text(), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_resource_aliases_resource_id", "resource_aliases", ["resource_id"])
    op.create_table(
        "operation_resource_rules",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "resource_id",
            sa.String(),
            sa.ForeignKey("resources.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("operation_key", sa.Text(), nullable=False),
        sa.Column("method", sa.String(), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("group_path", sa.Text(), nullable=False),
        sa.Column("has_resource", sa.Boolean(), nullable=False),
        sa.Column("resource_aliases", sa.JSON(), nullable=False),
        sa.Column("id_field_name", sa.Text(), nullable=True),
        sa.Column("id_selector", sa.Text(), nullable=True),
        sa.Column("access_mode", sa.String(), nullable=False),
        sa.Column("classification_source", sa.String(), nullable=False),
        sa.Column("id_observed", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("operation_key", "group_path", name="uq_operation_resource_rule"),
    )
    op.create_index(
        "ix_operation_resource_rules_resource_id",
        "operation_resource_rules",
        ["resource_id"],
    )
    op.create_table(
        "resource_identifiers",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "resource_id",
            sa.String(),
            sa.ForeignKey("resources.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("value_type", sa.String(), nullable=False),
        sa.Column("value_text", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "resource_id",
            "value_type",
            "value_text",
            name="uq_resource_identifier_value",
        ),
    )
    op.create_index(
        "ix_resource_identifiers_resource_id",
        "resource_identifiers",
        ["resource_id"],
    )
    op.create_table(
        "resource_operation_usages",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "identifier_id",
            sa.String(),
            sa.ForeignKey("resource_identifiers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "operation_rule_id",
            sa.String(),
            sa.ForeignKey("operation_resource_rules.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("access_mode", sa.String(), nullable=False),
        sa.Column("latest_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "identifier_id",
            "operation_rule_id",
            name="uq_resource_operation_usage",
        ),
    )
    op.create_index(
        "ix_resource_operation_usages_rule_id",
        "resource_operation_usages",
        ["operation_rule_id"],
    )
    op.create_table(
        "resource_monitor_errors",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "resource_id",
            sa.String(),
            sa.ForeignKey("resources.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("operation_key", sa.Text(), nullable=False),
        sa.Column("method", sa.String(), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("group_path", sa.Text(), nullable=False),
        sa.Column("code", sa.String(), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("issues", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("operation_key", "group_path", name="uq_resource_monitor_error"),
    )
    op.create_index(
        "ix_resource_monitor_errors_resource_id",
        "resource_monitor_errors",
        ["resource_id"],
    )


def downgrade() -> None:
    """
    Handle downgrade as part of the repository and database persistence boundary.

    The annotated arguments and return type define the data boundary used by callers.
    """
    op.drop_index("ix_resource_monitor_errors_resource_id", table_name="resource_monitor_errors")
    op.drop_table("resource_monitor_errors")
    op.drop_index("ix_resource_operation_usages_rule_id", table_name="resource_operation_usages")
    op.drop_table("resource_operation_usages")
    op.drop_index("ix_resource_identifiers_resource_id", table_name="resource_identifiers")
    op.drop_table("resource_identifiers")
    op.drop_index("ix_operation_resource_rules_resource_id", table_name="operation_resource_rules")
    op.drop_table("operation_resource_rules")
    op.drop_index("ix_resource_aliases_resource_id", table_name="resource_aliases")
    op.drop_table("resource_aliases")
    op.drop_table("resources")
