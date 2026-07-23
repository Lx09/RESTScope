"""create operation input generator configuration tables

Revision ID: 0002_create_generator_configs
Revises: 0001_create_schema_sources
Create Date: 2026-07-23
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0002_create_generator_configs"
down_revision = "0001_create_schema_sources"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "generator_catalog_state",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "operation_generator_configs",
        sa.Column("operation_key", sa.Text(), primary_key=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("disabled_reasons", sa.JSON(), nullable=False),
        sa.Column("active_media_type", sa.String()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "input_generator_configs",
        sa.Column("input_node_id", sa.String(), primary_key=True),
        sa.Column(
            "operation_key",
            sa.Text(),
            sa.ForeignKey("operation_generator_configs.operation_key", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("inclusion_probability", sa.Float(), nullable=False),
        sa.Column("strategy", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index(
        "ix_input_generator_configs_operation_key",
        "input_generator_configs",
        ["operation_key"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_input_generator_configs_operation_key",
        table_name="input_generator_configs",
    )
    op.drop_table("input_generator_configs")
    op.drop_table("operation_generator_configs")
    op.drop_table("generator_catalog_state")
