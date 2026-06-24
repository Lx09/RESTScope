"""ORM mapping for operation intelligence."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import ForeignKey, Index, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base, UpdatedAtMixin
from ..types import JsonType, StringList


class OperationIntelligenceORM(UpdatedAtMixin, Base):
    __tablename__ = "operation_intelligence"
    __table_args__ = (
        Index("idx_operation_intelligence_schema_risk", "schema_id", "dynamic_risk_score"),
        Index("idx_operation_intelligence_regression", "schema_id", "regression_priority"),
        Index("idx_operation_intelligence_state", "schema_id", "test_state"),
    )

    operation_id: Mapped[str] = mapped_column(ForeignKey("operations.id"), primary_key=True)
    schema_id: Mapped[str] = mapped_column(ForeignKey("schemas.id"), nullable=False)
    test_state: Mapped[str] = mapped_column(String, default="profiled", nullable=False)
    dynamic_risk_score: Mapped[Decimal] = mapped_column(Numeric, default=Decimal("0"), nullable=False)
    failure_density: Mapped[Decimal] = mapped_column(Numeric, default=Decimal("0"), nullable=False)
    flake_rate: Mapped[Decimal] = mapped_column(Numeric, default=Decimal("0"), nullable=False)
    last_tested_at: Mapped[datetime | None] = mapped_column()
    total_campaigns: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_cases_executed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    observation_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    confirmed_issue_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    server_error_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    contract_violation_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    semantic_violation_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    flake_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    learned_constraint_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    high_confidence_constraint_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    recommended_checks: Mapped[list[str]] = mapped_column(StringList, default=list, nullable=False)
    regression_priority: Mapped[Decimal] = mapped_column(Numeric, default=Decimal("0"), nullable=False)
    summary_json: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict, nullable=False)
