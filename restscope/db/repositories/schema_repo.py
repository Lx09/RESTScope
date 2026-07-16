from __future__ import annotations

from sqlalchemy import select

from ..orm import SchemaORM
from ..records import SchemaRecord
from .base_repo import BaseRepository


class SchemaRepository(BaseRepository[SchemaORM, SchemaRecord]):
    orm_class = SchemaORM
    record_class = SchemaRecord

    def get_by_hash(self, spec_hash: str) -> SchemaRecord | None:
        obj = self.session.scalar(select(SchemaORM).where(SchemaORM.spec_hash == spec_hash))
        return self.to_record(obj) if obj is not None else None

    def get_ready(self) -> SchemaRecord | None:
        obj = self.session.scalar(
            select(SchemaORM).where(SchemaORM.catalog_status == "ready")
        )
        return self.to_record(obj) if obj is not None else None

    def list_recent(self, *, limit: int = 20) -> list[SchemaRecord]:
        return self.to_records(
            self.session.scalars(
                select(SchemaORM).order_by(SchemaORM.created_at.desc()).limit(limit)
            ).all()
        )
