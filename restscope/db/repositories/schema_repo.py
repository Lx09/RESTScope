"""SQLAlchemy schema repository adapter."""

from __future__ import annotations

from typing import cast

from sqlalchemy import select
from sqlalchemy.orm import Session

from restscope.catalog import SchemaRecord

from ..orm import SchemaORM
from ..time import as_utc


class SqlAlchemySchemaRepository:
    """Implement the schema repository port with SQLAlchemy."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, *, id: str, file_path: str | None, raw_content: str | None) -> SchemaRecord:
        """
        Add one validated record to the repository and database persistence boundary.

        The class owns any required collaborators or state; arguments supply only the
        data needed for this call.
        """
        obj = SchemaORM(
            id=id,
            file_path=file_path,
            raw_content=raw_content,
        )
        self.session.add(obj)
        self.session.flush()
        return self._to_record(obj)

    def get(self, schema_id: str) -> SchemaRecord | None:
        """
        Handle get as part of the repository and database persistence boundary.

        The class owns any required collaborators or state; arguments supply only the
        data needed for this call.
        """
        obj = cast(SchemaORM | None, self.session.get(SchemaORM, schema_id))
        return self._to_record(obj) if obj is not None else None

    def list(self) -> list[SchemaRecord]:
        """
        Handle list as part of the repository and database persistence boundary.

        The class owns any required collaborators or state; arguments supply only the
        data needed for this call.
        """
        objects = self.session.scalars(
            select(SchemaORM).order_by(SchemaORM.created_at, SchemaORM.id)
        ).all()
        return [self._to_record(obj) for obj in objects]

    def replace_source(
        self,
        schema_id: str,
        *,
        file_path: str | None,
        raw_content: str | None,
    ) -> SchemaRecord | None:
        """
        Handle replace source as part of the repository and database persistence
        boundary.

        The class owns any required collaborators or state; arguments supply only the
        data needed for this call.
        """
        obj = cast(SchemaORM | None, self.session.get(SchemaORM, schema_id))
        if obj is None:
            return None
        obj.file_path = file_path
        obj.raw_content = raw_content
        self.session.flush()
        self.session.refresh(obj)
        return self._to_record(obj)

    @staticmethod
    def _to_record(obj: SchemaORM) -> SchemaRecord:
        return SchemaRecord(
            id=obj.id,
            file_path=obj.file_path,
            raw_content=obj.raw_content,
            created_at=as_utc(obj.created_at),
            updated_at=as_utc(obj.updated_at),
        )
