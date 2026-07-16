"""Persisted operation-to-operation relationships derived from OpenAPI."""

from __future__ import annotations

from sqlalchemy import Float, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base, CreatedAtMixin


class OperationEdgeORM(CreatedAtMixin, Base):
    __tablename__ = "operation_edges"
    __table_args__ = (
        Index("idx_operation_edges_schema", "schema_id"),
        Index("idx_operation_edges_source", "source_operation_id"),
        Index("idx_operation_edges_target", "target_operation_id"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    schema_id: Mapped[str] = mapped_column(ForeignKey("schemas.id"), nullable=False)
    source_operation_id: Mapped[str] = mapped_column(ForeignKey("operations.id"), nullable=False)
    target_operation_id: Mapped[str] = mapped_column(ForeignKey("operations.id"), nullable=False)
    edge_type: Mapped[str] = mapped_column(String, nullable=False)
    value: Mapped[str | None] = mapped_column(String)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    reason: Mapped[str] = mapped_column(String, nullable=False)
