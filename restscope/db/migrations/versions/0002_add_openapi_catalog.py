"""add one-time OpenAPI catalog persistence

Revision ID: 0002_add_openapi_catalog
Revises: 0001_create_mvp_tables
Create Date: 2026-07-15
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0002_add_openapi_catalog"
down_revision = "0001_create_mvp_tables"
branch_labels = None
depends_on = None


def _json_type():
    return postgresql.JSONB() if op.get_bind().dialect.name == "postgresql" else sa.JSON()


def upgrade() -> None:
    json_type = _json_type()
    with op.batch_alter_table("schemas") as batch_op:
        batch_op.add_column(sa.Column("normalized_spec_json", json_type))
        batch_op.add_column(
            sa.Column(
                "parse_diagnostics_json",
                json_type,
                nullable=False,
                server_default=sa.text("'{}'"),
            )
        )
        batch_op.add_column(
            sa.Column("catalog_status", sa.String(), nullable=False, server_default="legacy")
        )
        batch_op.add_column(sa.Column("catalog_slot", sa.String()))
        batch_op.add_column(sa.Column("parser_version", sa.String()))
        batch_op.add_column(sa.Column("initialized_at", sa.DateTime(timezone=True)))
        batch_op.create_index("idx_schemas_catalog_slot", ["catalog_slot"], unique=True)
        batch_op.create_check_constraint(
            "ck_schemas_schemas_ready_catalog_slot",
            "catalog_status != 'ready' OR (catalog_slot IS NOT NULL AND catalog_slot = 'default')",
        )

    op.create_table(
        "operation_edges",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("schema_id", sa.String(), sa.ForeignKey("schemas.id"), nullable=False),
        sa.Column(
            "source_operation_id",
            sa.String(),
            sa.ForeignKey("operations.id"),
            nullable=False,
        ),
        sa.Column(
            "target_operation_id",
            sa.String(),
            sa.ForeignKey("operations.id"),
            nullable=False,
        ),
        sa.Column("edge_type", sa.String(), nullable=False),
        sa.Column("value", sa.String()),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("reason", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("idx_operation_edges_schema", "operation_edges", ["schema_id"])
    op.create_index("idx_operation_edges_source", "operation_edges", ["source_operation_id"])
    op.create_index("idx_operation_edges_target", "operation_edges", ["target_operation_id"])


def downgrade() -> None:
    op.drop_table("operation_edges")
    with op.batch_alter_table("schemas") as batch_op:
        batch_op.drop_constraint("ck_schemas_schemas_ready_catalog_slot", type_="check")
        batch_op.drop_index("idx_schemas_catalog_slot")
        batch_op.drop_column("initialized_at")
        batch_op.drop_column("parser_version")
        batch_op.drop_column("catalog_slot")
        batch_op.drop_column("catalog_status")
        batch_op.drop_column("parse_diagnostics_json")
        batch_op.drop_column("normalized_spec_json")
