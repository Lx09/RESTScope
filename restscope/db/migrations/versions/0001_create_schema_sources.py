"""create schema source table

Revision ID: 0001_create_schema_sources
Revises:
Create Date: 2026-07-19
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0001_create_schema_sources"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "schemas",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("file_path", sa.Text()),
        sa.Column("raw_content", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "(file_path IS NOT NULL AND raw_content IS NULL) "
            "OR (file_path IS NULL AND raw_content IS NOT NULL)",
            name="ck_schemas_source_exactly_one",
        ),
    )


def downgrade() -> None:
    op.drop_table("schemas")
