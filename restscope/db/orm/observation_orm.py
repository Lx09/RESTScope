"""ORM mapping for test observations."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import ForeignKey, Index, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base
from ..time import utc_now
from ..types import JsonType


class TestObservationORM(Base):
    __tablename__ = "test_observations"
    __table_args__ = (
        Index("idx_test_observations_dedupe", "schema_id", "dedupe_key", unique=True),
        Index("idx_test_observations_operation", "schema_id", "operation_id"),
        Index("idx_test_observations_type", "schema_id", "observation_type"),
        Index("idx_test_observations_status", "schema_id", "status"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("agent_tasks.id"), nullable=False)
    campaign_id: Mapped[str] = mapped_column(ForeignKey("campaigns.id"), nullable=False)
    schema_id: Mapped[str] = mapped_column(ForeignKey("schemas.id"), nullable=False)
    operation_id: Mapped[str | None] = mapped_column(ForeignKey("operations.id"))
    observation_type: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, default="observed", nullable=False)
    severity: Mapped[str] = mapped_column(String, nullable=False)
    confidence: Mapped[Decimal] = mapped_column(Numeric, default=Decimal("0.5"), nullable=False)
    dedupe_key: Mapped[str] = mapped_column(String, nullable=False)
    check_id: Mapped[str | None] = mapped_column(String)
    request_fingerprint: Mapped[str | None] = mapped_column(String)
    response_fingerprint: Mapped[str | None] = mapped_column(String)
    request_summary_json: Mapped[dict[str, Any] | None] = mapped_column(JsonType)
    response_summary_json: Mapped[dict[str, Any] | None] = mapped_column(JsonType)
    reproducer_artifact_id: Mapped[str | None] = mapped_column(String)
    raw_artifact_id: Mapped[str | None] = mapped_column(String)
    hypothesis: Mapped[str | None] = mapped_column(String)
    first_seen_at: Mapped[datetime] = mapped_column(default=utc_now, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(default=utc_now, nullable=False)
    occurrence_count: Mapped[int] = mapped_column(default=1, nullable=False)
