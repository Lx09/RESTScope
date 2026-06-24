"""ORM mapping for agent tasks."""

from __future__ import annotations

from typing import Any

from sqlalchemy import ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base, CreatedAtMixin, UpdatedAtMixin
from ..types import JsonType, StringList


class AgentTaskORM(CreatedAtMixin, UpdatedAtMixin, Base):
    __tablename__ = "agent_tasks"
    __table_args__ = (
        Index("idx_agent_tasks_schema", "schema_id"),
        Index("idx_agent_tasks_state", "state"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    schema_id: Mapped[str] = mapped_column(ForeignKey("schemas.id"), nullable=False)
    target_env_id: Mapped[str | None] = mapped_column(String)
    state: Mapped[str] = mapped_column(String, nullable=False)
    goal_json: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False)
    budget_json: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False)
    cycle_index: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    active_campaign_id: Mapped[str | None] = mapped_column(String)
    selected_operation_ids: Mapped[list[str]] = mapped_column(StringList, default=list, nullable=False)
    current_hypotheses: Mapped[list[str]] = mapped_column(StringList, default=list, nullable=False)
    current_check_ids: Mapped[list[str]] = mapped_column(StringList, default=list, nullable=False)
    context_snapshot_id: Mapped[str | None] = mapped_column(String)
    latest_report_uri: Mapped[str | None] = mapped_column(String)
    blockers_json: Mapped[list[Any]] = mapped_column(JsonType, default=list, nullable=False)
    last_error: Mapped[str | None] = mapped_column(String)
    version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
