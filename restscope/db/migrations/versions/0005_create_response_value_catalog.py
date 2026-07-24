"""create response value catalog

Revision ID: 0005_create_response_value_catalog
Revises: 0004_create_generator_revision_history
Create Date: 2026-07-24
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0005_create_response_value_catalog"
down_revision = "0004_create_generator_revision_history"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "response_value_monitors",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("value_name", sa.Text(), nullable=False, unique=True),
        sa.Column("consumer_operation_key", sa.Text(), nullable=False),
        sa.Column("consumer_input_node_id", sa.Text(), nullable=False),
        sa.Column("parameter_name", sa.Text(), nullable=False),
        sa.Column("expected_type", sa.String(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False),
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
            "consumer_operation_key",
            "consumer_input_node_id",
            name="uq_response_value_monitor_consumer_input",
        ),
    )
    op.create_table(
        "response_value_sources",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "monitor_id",
            sa.String(),
            sa.ForeignKey("response_value_monitors.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("producer_operation_key", sa.Text(), nullable=False),
        sa.Column("status_code", sa.String(), nullable=False),
        sa.Column("media_type", sa.Text(), nullable=False),
        sa.Column("selector", sa.Text(), nullable=False),
        sa.Column("field_name", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "monitor_id",
            "producer_operation_key",
            "status_code",
            "media_type",
            "selector",
            name="uq_response_value_source",
        ),
    )
    op.create_index(
        "ix_response_value_sources_monitor_id",
        "response_value_sources",
        ["monitor_id"],
    )
    op.create_table(
        "response_values",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "monitor_id",
            sa.String(),
            sa.ForeignKey("response_value_monitors.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("value_type", sa.String(), nullable=False),
        sa.Column("value_text", sa.Text(), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "monitor_id",
            "value_type",
            "value_text",
            name="uq_response_value_typed_value",
        ),
    )
    op.create_index(
        "ix_response_values_monitor_id",
        "response_values",
        ["monitor_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_response_values_monitor_id", table_name="response_values")
    op.drop_table("response_values")
    op.drop_index(
        "ix_response_value_sources_monitor_id",
        table_name="response_value_sources",
    )
    op.drop_table("response_value_sources")
    op.drop_table("response_value_monitors")
