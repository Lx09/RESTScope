"""ORM mapping for event log."""

from __future__ import annotations

from typing import Any

from sqlalchemy import Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base, CreatedAtMixin
from ..types import JsonType


class EventLogORM(CreatedAtMixin, Base):
    __tablename__ = "event_log"
    __table_args__ = (
        Index("idx_event_log_task", "task_id", "created_at"),
        Index("idx_event_log_campaign", "campaign_id", "created_at"),
        Index("idx_event_log_type", "event_type", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str | None] = mapped_column(String)
    campaign_id: Mapped[str | None] = mapped_column(String)
    event_type: Mapped[str] = mapped_column(String, nullable=False)
    actor: Mapped[str] = mapped_column(String, nullable=False)
    from_state: Mapped[str | None] = mapped_column(String)
    to_state: Mapped[str | None] = mapped_column(String)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False)
