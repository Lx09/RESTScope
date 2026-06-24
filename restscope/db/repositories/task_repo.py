from __future__ import annotations

from sqlalchemy import select

from ..exceptions import ConcurrencyError
from ..orm import AgentTaskORM
from ..records import AgentTaskRecord
from .base_repo import BaseRepository


class AgentTaskRepository(BaseRepository[AgentTaskORM, AgentTaskRecord]):
    orm_class = AgentTaskORM
    record_class = AgentTaskRecord

    def list_by_state(self, state: str) -> list[AgentTaskRecord]:
        return self.to_records(
            self.session.scalars(select(AgentTaskORM).where(AgentTaskORM.state == state)).all()
        )

    def transition_state(
        self,
        *,
        task_id: str,
        expected_state: str,
        expected_version: int,
        new_state: str,
    ) -> AgentTaskRecord:
        obj = self.session.get(AgentTaskORM, task_id)
        if obj is None or obj.state != expected_state or obj.version != expected_version:
            raise ConcurrencyError(f"stale task transition for {task_id}")
        obj.state = new_state
        obj.version += 1
        self.session.flush()
        return self.to_record(obj)
