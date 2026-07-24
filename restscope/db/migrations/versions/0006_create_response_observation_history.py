"""create response observation history

Revision ID: 0006_create_response_observation_history
Revises: 0005_create_response_value_catalog
Create Date: 2026-07-24
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0006_create_response_observation_history"
down_revision = "0005_create_response_value_catalog"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "response_observations",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("operation_key", sa.Text(), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=False),
        sa.Column("media_type", sa.Text(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_response_observations_operation_key",
        "response_observations",
        ["operation_key"],
    )
    op.create_table(
        "response_observation_scalars",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "observation_id",
            sa.String(),
            sa.ForeignKey("response_observations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("selector", sa.Text(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("value_type", sa.String(), nullable=False),
        sa.Column("value_text", sa.Text(), nullable=False),
        sa.UniqueConstraint(
            "observation_id",
            "selector",
            "value_type",
            "value_text",
            name="uq_response_observation_scalar_value",
        ),
    )
    op.create_index(
        "ix_response_observation_scalars_observation_id",
        "response_observation_scalars",
        ["observation_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_response_observation_scalars_observation_id",
        table_name="response_observation_scalars",
    )
    op.drop_table("response_observation_scalars")
    op.drop_index(
        "ix_response_observations_operation_key",
        table_name="response_observations",
    )
    op.drop_table("response_observations")
