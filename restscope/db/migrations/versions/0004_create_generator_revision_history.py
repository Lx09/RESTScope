"""create generator revision history

Revision ID: 0004_create_generator_revision_history
Revises: 0003_create_resource_catalog
Create Date: 2026-07-24
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0004_create_generator_revision_history"
down_revision = "0003_create_resource_catalog"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """
    Handle upgrade as part of the repository and database persistence boundary.

    The annotated arguments and return type define the data boundary used by callers.
    """
    op.create_table(
        "generator_config_revisions",
        sa.Column(
            "operation_key",
            sa.Text(),
            sa.ForeignKey(
                "operation_generator_configs.operation_key",
                ondelete="CASCADE",
            ),
            primary_key=True,
        ),
        sa.Column("revision", sa.Integer(), primary_key=True),
        sa.Column("parent_revision", sa.Integer()),
        sa.Column("lifecycle", sa.String(length=20), nullable=False),
        sa.Column("rollback_of_revision", sa.Integer()),
        sa.Column("restored_from_revision", sa.Integer()),
        sa.Column("hypothesis", sa.JSON()),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("evaluation", sa.JSON()),
        sa.Column("evaluated_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    operation_configs = sa.table(
        "operation_generator_configs",
        sa.column("operation_key", sa.Text()),
        sa.column("revision", sa.Integer()),
        sa.column("snapshot", sa.JSON()),
        sa.column("enabled", sa.Boolean()),
        sa.column("disabled_reasons", sa.JSON()),
        sa.column("active_media_type", sa.String()),
    )
    input_configs = sa.table(
        "input_generator_configs",
        sa.column("input_node_id", sa.String()),
        sa.column("operation_key", sa.Text()),
        sa.column("position", sa.Integer()),
        sa.column("inclusion_probability", sa.Float()),
        sa.column("strategy", sa.JSON()),
    )
    revisions = sa.table(
        "generator_config_revisions",
        sa.column("operation_key", sa.Text()),
        sa.column("revision", sa.Integer()),
        sa.column("parent_revision", sa.Integer()),
        sa.column("lifecycle", sa.String()),
        sa.column("config", sa.JSON()),
    )

    connection = op.get_bind()
    operations = connection.execute(
        sa.select(operation_configs).order_by(
            operation_configs.c.operation_key
        )
    ).mappings()
    rows: list[dict] = []
    for operation in operations:
        inputs = connection.execute(
            sa.select(input_configs)
            .where(
                input_configs.c.operation_key
                == operation["operation_key"]
            )
            .order_by(input_configs.c.position)
        ).mappings()
        rows.append(
            {
                "operation_key": operation["operation_key"],
                "revision": operation["revision"],
                "parent_revision": None,
                "lifecycle": "accepted",
                "config": {
                    "operation_key": operation["operation_key"],
                    "revision": operation["revision"],
                    "snapshot": operation["snapshot"],
                    "enabled": operation["enabled"],
                    "disabled_reasons": operation["disabled_reasons"],
                    "active_media_type": operation["active_media_type"],
                    "configs": [
                        {
                            "input_node_id": item["input_node_id"],
                            "inclusion_probability": item[
                                "inclusion_probability"
                            ],
                            "strategy": item["strategy"],
                        }
                        for item in inputs
                    ],
                },
            }
        )
    if rows:
        connection.execute(revisions.insert(), rows)


def downgrade() -> None:
    """
    Handle downgrade as part of the repository and database persistence boundary.

    The annotated arguments and return type define the data boundary used by callers.
    """
    op.drop_table("generator_config_revisions")
