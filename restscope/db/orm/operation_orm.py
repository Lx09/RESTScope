"""ORM mapping for operations."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import ForeignKey, Index, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base, CreatedAtMixin
from ..types import JsonType, StringList


class OperationORM(CreatedAtMixin, Base):
    __tablename__ = "operations"
    __table_args__ = (
        Index("idx_operations_schema", "schema_id"),
        Index("idx_operations_method_path", "schema_id", "method", "path"),
        Index("idx_operations_static_risk", "schema_id", "static_risk_score"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    schema_id: Mapped[str] = mapped_column(ForeignKey("schemas.id"), nullable=False)
    operation_id: Mapped[str | None] = mapped_column(String)
    method: Mapped[str] = mapped_column(String, nullable=False)
    path: Mapped[str] = mapped_column(String, nullable=False)
    tags: Mapped[list[str]] = mapped_column(StringList, default=list, nullable=False)
    summary: Mapped[str | None] = mapped_column(String)
    resource: Mapped[str | None] = mapped_column(String)
    mutability: Mapped[str | None] = mapped_column(String)
    security: Mapped[dict[str, Any] | None] = mapped_column(JsonType)
    request_schema_refs: Mapped[list[str]] = mapped_column(StringList, default=list, nullable=False)
    response_schema_refs: Mapped[list[str]] = mapped_column(StringList, default=list, nullable=False)
    card_json: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False)
    static_risk_score: Mapped[Decimal] = mapped_column(Numeric, default=Decimal("0"), nullable=False)
