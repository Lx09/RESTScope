"""Base repository helpers."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Generic, TypeVar

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..exceptions import NotFoundError


OrmT = TypeVar("OrmT")
RecordT = TypeVar("RecordT")


class BaseRepository(Generic[OrmT, RecordT]):
    """Shared CRUD helpers for repositories."""

    orm_class: type[OrmT]
    record_class: type[RecordT]

    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, **values: Any) -> RecordT:
        obj = self.orm_class(**values)
        self.session.add(obj)
        self.session.flush()
        return self.to_record(obj)

    def get(self, record_id: Any) -> RecordT | None:
        obj = self.session.get(self.orm_class, record_id)
        return self.to_record(obj) if obj is not None else None

    def require(self, record_id: Any) -> RecordT:
        record = self.get(record_id)
        if record is None:
            raise NotFoundError(f"{self.orm_class.__name__} not found: {record_id}")
        return record

    def list(self, *, limit: int | None = None) -> list[RecordT]:
        statement = select(self.orm_class)
        if limit is not None:
            statement = statement.limit(limit)
        return self.to_records(self.session.scalars(statement).all())

    def delete(self, record_id: Any) -> None:
        obj = self.session.get(self.orm_class, record_id)
        if obj is None:
            raise NotFoundError(f"{self.orm_class.__name__} not found: {record_id}")
        self.session.delete(obj)
        self.session.flush()

    def to_record(self, obj: OrmT) -> RecordT:
        values = {
            name: getattr(obj, name)
            for name in self.record_class.__dataclass_fields__
        }
        return self.record_class(**values)

    def to_records(self, objects: Sequence[OrmT]) -> list[RecordT]:
        return [self.to_record(obj) for obj in objects]
