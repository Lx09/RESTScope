from __future__ import annotations

from sqlalchemy import select

from ..orm import OperationORM
from ..records import OperationRecord
from .base_repo import BaseRepository


class OperationRepository(BaseRepository[OperationORM, OperationRecord]):
    orm_class = OperationORM
    record_class = OperationRecord

    def list_by_schema(self, schema_id: str) -> list[OperationRecord]:
        return self.to_records(
            self.session.scalars(select(OperationORM).where(OperationORM.schema_id == schema_id)).all()
        )

    def get_by_schema_method_path(
        self,
        schema_id: str,
        method: str,
        path: str,
    ) -> OperationRecord | None:
        obj = self.session.scalar(
            select(OperationORM).where(
                OperationORM.schema_id == schema_id,
                OperationORM.method == method,
                OperationORM.path == path,
            )
        )
        return self.to_record(obj) if obj is not None else None
