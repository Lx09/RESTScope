"""ORM mapping for schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base, CreatedAtMixin
from ..types import JsonType


class SchemaORM(CreatedAtMixin, Base):
    __tablename__ = "schemas"
    __table_args__ = (
        Index("idx_schemas_catalog_slot", "catalog_slot", unique=True),
        CheckConstraint(
            "catalog_status != 'ready' OR (catalog_slot IS NOT NULL AND catalog_slot = 'default')",
            name="schemas_ready_catalog_slot",
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    version: Mapped[str | None] = mapped_column(String)
    spec_hash: Mapped[str] = mapped_column(String, nullable=False, unique=True, index=True)
    raw_spec_uri: Mapped[str] = mapped_column(String, nullable=False)
    normalized_spec_uri: Mapped[str | None] = mapped_column(String)
    openapi_version: Mapped[str | None] = mapped_column(String)
    operation_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    normalized_spec_json: Mapped[dict[str, Any] | None] = mapped_column(JsonType)
    parse_diagnostics_json: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict, nullable=False)
    catalog_status: Mapped[str] = mapped_column(String, default="legacy", nullable=False)
    catalog_slot: Mapped[str | None] = mapped_column(String)
    parser_version: Mapped[str | None] = mapped_column(String)
    initialized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
