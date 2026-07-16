from __future__ import annotations

from sqlalchemy import select

from ..orm import OperationEdgeORM
from ..records import OperationEdgeRecord
from .base_repo import BaseRepository


class OperationEdgeRepository(BaseRepository[OperationEdgeORM, OperationEdgeRecord]):
    orm_class = OperationEdgeORM
    record_class = OperationEdgeRecord

    def list_by_schema(self, schema_id: str) -> list[OperationEdgeRecord]:
        return self.to_records(
            self.session.scalars(
                select(OperationEdgeORM).where(OperationEdgeORM.schema_id == schema_id)
            ).all()
        )
