from __future__ import annotations

from sqlalchemy import select

from ..orm import OperationIntelligenceORM
from ..records import OperationIntelligenceRecord
from .base_repo import BaseRepository


class OperationIntelligenceRepository(
    BaseRepository[OperationIntelligenceORM, OperationIntelligenceRecord]
):
    orm_class = OperationIntelligenceORM
    record_class = OperationIntelligenceRecord

    def get_by_operation(self, operation_id: str) -> OperationIntelligenceRecord | None:
        return self.get(operation_id)

    def list_high_risk(self, schema_id: str, *, limit: int = 20) -> list[OperationIntelligenceRecord]:
        return self.to_records(
            self.session.scalars(
                select(OperationIntelligenceORM)
                .where(OperationIntelligenceORM.schema_id == schema_id)
                .order_by(OperationIntelligenceORM.dynamic_risk_score.desc())
                .limit(limit)
            ).all()
        )
