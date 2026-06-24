from __future__ import annotations

from typing import Any

from sqlalchemy import select

from ..orm import TestObservationORM
from ..records import TestObservationRecord
from ..time import utc_now
from .base_repo import BaseRepository


class TestObservationRepository(BaseRepository[TestObservationORM, TestObservationRecord]):
    orm_class = TestObservationORM
    record_class = TestObservationRecord

    def get_by_dedupe_key(self, schema_id: str, dedupe_key: str) -> TestObservationRecord | None:
        obj = self.session.scalar(
            select(TestObservationORM).where(
                TestObservationORM.schema_id == schema_id,
                TestObservationORM.dedupe_key == dedupe_key,
            )
        )
        return self.to_record(obj) if obj is not None else None

    def list_by_schema_status(
        self,
        schema_id: str,
        statuses: list[str],
        *,
        limit: int = 50,
    ) -> list[TestObservationRecord]:
        statement = (
            select(TestObservationORM)
            .where(TestObservationORM.schema_id == schema_id)
            .order_by(TestObservationORM.last_seen_at.desc())
            .limit(limit)
        )
        if statuses:
            statement = statement.where(TestObservationORM.status.in_(statuses))
        return self.to_records(self.session.scalars(statement).all())

    def list_recent_for_operations(
        self,
        schema_id: str,
        operation_ids: list[str],
        *,
        limit: int = 50,
    ) -> list[TestObservationRecord]:
        if not operation_ids:
            return []
        return self.to_records(
            self.session.scalars(
                select(TestObservationORM)
                .where(
                    TestObservationORM.schema_id == schema_id,
                    TestObservationORM.operation_id.in_(operation_ids),
                )
                .order_by(TestObservationORM.last_seen_at.desc())
                .limit(limit)
            ).all()
        )

    def upsert_observed(self, **values: Any) -> TestObservationRecord:
        existing = self.session.scalar(
            select(TestObservationORM).where(
                TestObservationORM.schema_id == values["schema_id"],
                TestObservationORM.dedupe_key == values["dedupe_key"],
            )
        )
        if existing is not None:
            existing.occurrence_count += 1
            existing.last_seen_at = utc_now()
            self.session.flush()
            return self.to_record(existing)
        return self.add(**values)
