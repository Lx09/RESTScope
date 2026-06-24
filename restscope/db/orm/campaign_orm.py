"""ORM mapping for campaigns."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base, CreatedAtMixin
from ..types import JsonType


class CampaignORM(CreatedAtMixin, Base):
    __tablename__ = "campaigns"
    __table_args__ = (
        Index("idx_campaigns_task", "task_id"),
        Index("idx_campaigns_schema_status", "schema_id", "status"),
        Index("idx_campaigns_type", "schema_id", "campaign_type"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("agent_tasks.id"), nullable=False)
    schema_id: Mapped[str] = mapped_column(ForeignKey("schemas.id"), nullable=False)
    target_env_id: Mapped[str | None] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, nullable=False)
    campaign_type: Mapped[str] = mapped_column(String, nullable=False)
    campaign_spec_json: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False)
    validation_result_json: Mapped[dict[str, Any] | None] = mapped_column(JsonType)
    summary_json: Mapped[dict[str, Any] | None] = mapped_column(JsonType)
    started_at: Mapped[datetime | None] = mapped_column()
    finished_at: Mapped[datetime | None] = mapped_column()
    artifact_bundle_uri: Mapped[str | None] = mapped_column(String)
