"""SQLAlchemy adapter for normalized OpenAPI current state and change events."""

from __future__ import annotations

from copy import deepcopy
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from restscope.openapi_audit import OpenAPIChangeEventRecord, OpenAPIChangeEventWrite

from ..orm import OpenAPIChangeEventORM, OpenAPICurrentORM
from ..time import as_utc
from ._transaction import _SqlAlchemyUnitOfWork


class SqlAlchemyOpenAPIRepository:
    """Persist the singleton document and append-only response change audit."""

    def __init__(self, session: Session) -> None:
        """Use the caller-owned session for every read and write."""

        self.session = session
    def initialize(self, document: dict[str, object]) -> None:
        """Insert the only allowed current-document row."""

        if self.session.get(OpenAPICurrentORM, 1) is not None:
            raise ValueError("The OpenAPI catalog is already initialized")
        self.session.add(OpenAPICurrentORM(singleton_id=1, document=deepcopy(document)))
        try:
            self.session.flush()
        except IntegrityError as exc:
            raise ValueError("The OpenAPI catalog is already initialized") from exc

    def get_current(self) -> dict[str, object] | None:
        """Return the current document without exposing ORM-managed state."""

        row = self.session.get(OpenAPICurrentORM, 1)
        return deepcopy(row.document) if row is not None else None

    def record_change(
        self,
        *,
        document: dict[str, object],
        event: OpenAPIChangeEventWrite,
    ) -> OpenAPIChangeEventRecord:
        """Update the singleton and insert one event in the active transaction."""

        current = self.session.get(OpenAPICurrentORM, 1)
        if current is None:
            raise RuntimeError("The OpenAPI catalog has not been initialized")
        current.document = deepcopy(document)
        row = OpenAPIChangeEventORM(
            id=f"openapi_change_{uuid4().hex}",
            operation_id=event.operation_key,
            status_code=event.status_code,
            media_type=event.media_type,
            changes=list(event.changes),
            response_before=deepcopy(event.response_before),
            response_after=deepcopy(event.response_after),
        )
        self.session.add(row)
        self.session.flush()
        return _event_record(row)

    def list_changes(
        self,
        operation_key: str | None = None,
    ) -> list[OpenAPIChangeEventRecord]:
        """Return events in durable creation order."""

        query = select(OpenAPIChangeEventORM)
        if operation_key is not None:
            query = query.where(OpenAPIChangeEventORM.operation_id == operation_key)
        rows = self.session.scalars(
            query.order_by(OpenAPIChangeEventORM.created_at, OpenAPIChangeEventORM.id)
        ).all()
        return [_event_record(row) for row in rows]


def _event_record(row: OpenAPIChangeEventORM) -> OpenAPIChangeEventRecord:
    """Project one ORM row into the immutable public audit contract."""

    return OpenAPIChangeEventRecord(
        id=row.id,
        operation_key=row.operation_id,
        status_code=row.status_code,
        media_type=row.media_type,
        changes=list(row.changes),
        response_before=deepcopy(row.response_before),
        response_after=deepcopy(row.response_after),
        created_at=as_utc(row.created_at),
    )


class SqlAlchemyOpenAPIUnitOfWork(_SqlAlchemyUnitOfWork):
    """Open one transaction for current OpenAPI state and its change audit."""

    def __enter__(self) -> "SqlAlchemyOpenAPIUnitOfWork":
        """Open the session and bind the OpenAPI repository to it."""

        self.openapi = SqlAlchemyOpenAPIRepository(self._open_session())
        return self
