from __future__ import annotations

from typing import Any

from sqlalchemy import select

from ..orm import EventLogORM
from ..records import EventLogRecord
from .base_repo import BaseRepository


class EventLogRepository(BaseRepository[EventLogORM, EventLogRecord]):
    orm_class = EventLogORM
    record_class = EventLogRecord

    def append(self, **values: Any) -> EventLogRecord:
        return self.add(**values)

    def list_by_task(self, task_id: str, *, limit: int | None = None) -> list[EventLogRecord]:
        statement = (
            select(EventLogORM)
            .where(EventLogORM.task_id == task_id)
            .order_by(EventLogORM.created_at.desc())
        )
        if limit is not None:
            statement = statement.limit(limit)
        return self.to_records(
            self.session.scalars(statement).all()
        )

    def list_by_campaign(self, campaign_id: str, *, limit: int | None = None) -> list[EventLogRecord]:
        statement = (
            select(EventLogORM)
            .where(EventLogORM.campaign_id == campaign_id)
            .order_by(EventLogORM.created_at.desc())
        )
        if limit is not None:
            statement = statement.limit(limit)
        return self.to_records(
            self.session.scalars(statement).all()
        )

    def delete(self, record_id: Any) -> None:
        raise TypeError("event_log is append-only")
