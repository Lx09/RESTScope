"""ORM mapping for durable OpenAPI sources."""

from __future__ import annotations

from sqlalchemy import CheckConstraint, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base, CreatedAtMixin, UpdatedAtMixin


class SchemaORM(CreatedAtMixin, UpdatedAtMixin, Base):
    __tablename__ = "schemas"
    __table_args__ = (
        CheckConstraint(
            "(file_path IS NOT NULL AND raw_content IS NULL) "
            "OR (file_path IS NULL AND raw_content IS NOT NULL)",
            name="source_exactly_one",
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    file_path: Mapped[str | None] = mapped_column(Text)
    raw_content: Mapped[str | None] = mapped_column(Text)
