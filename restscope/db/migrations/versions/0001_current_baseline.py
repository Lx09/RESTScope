"""Create the complete current RESTScope database from an empty file.

Revision ID: 0001_current_baseline
Revises:
Create Date: 2026-07-29

RESTScope deliberately rejects pre-existing database files during App startup.
There is therefore no supported upgrade path from the exploratory 0001–0006
chain.  This single baseline creates OpenAPI, Generator, API Behavior Monitor,
and Operation Smoke Memory tables exactly as the current ORM declares them.
"""

from __future__ import annotations

from alembic import op

from restscope.db import orm  # noqa: F401  # Register every current mapping.
from restscope.db.base import Base


revision = "0001_current_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create every current table in dependency order.

    Alembic already owns the ``alembic_version`` table.  SQLAlchemy metadata
    contains only application tables, so ``create_all`` does not alter
    Alembic's bookkeeping.
    """
    Base.metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    """Drop every current application table while retaining Alembic metadata."""
    Base.metadata.drop_all(bind=op.get_bind())
