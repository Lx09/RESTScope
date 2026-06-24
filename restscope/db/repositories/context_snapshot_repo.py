from __future__ import annotations

from sqlalchemy import select

from ..orm import ContextSnapshotORM
from ..records import ContextSnapshotRecord
from .base_repo import BaseRepository


class ContextSnapshotRepository(BaseRepository[ContextSnapshotORM, ContextSnapshotRecord]):
    orm_class = ContextSnapshotORM
    record_class = ContextSnapshotRecord

    def list_by_task(self, task_id: str) -> list[ContextSnapshotRecord]:
        return self.to_records(
            self.session.scalars(
                select(ContextSnapshotORM)
                .where(ContextSnapshotORM.task_id == task_id)
                .order_by(ContextSnapshotORM.cycle_index)
            ).all()
        )

    def get_latest_by_task_role(self, task_id: str, role: str) -> ContextSnapshotRecord | None:
        obj = self.session.scalar(
            select(ContextSnapshotORM)
            .where(ContextSnapshotORM.task_id == task_id, ContextSnapshotORM.role == role)
            .order_by(ContextSnapshotORM.cycle_index.desc(), ContextSnapshotORM.created_at.desc())
            .limit(1)
        )
        return self.to_record(obj) if obj is not None else None
