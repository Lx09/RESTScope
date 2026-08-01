"""Transactional service for the current OpenAPI document and change audit."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from .models import OpenAPIChangeEventRecord, OpenAPIChangeEventWrite
from .ports import OpenAPIUnitOfWorkFactory


class OpenAPICatalog:
    """Persist and inspect the normalized document owned by one App run."""

    def __init__(self, unit_of_work_factory: OpenAPIUnitOfWorkFactory) -> None:
        """Store the transaction factory without opening a database session."""

        self.unit_of_work_factory = unit_of_work_factory

    def initialize(self, document: dict[str, Any]) -> None:
        """Insert the singleton initial document.

        ``document`` must already be produced by the normalized IR-to-spec
        builder.  Reinitialization is rejected by the repository rather than
        silently replacing the API bound to this one-shot database.
        """

        with self.unit_of_work_factory() as uow:
            uow.openapi.initialize(deepcopy(document))
            uow.commit()

    def current_document(self) -> dict[str, Any]:
        """Return an isolated copy of the current persisted document."""

        with self.unit_of_work_factory() as uow:
            document = uow.openapi.get_current()
        if document is None:
            raise RuntimeError("The OpenAPI catalog has not been initialized")
        return deepcopy(document)

    def record_change(
        self,
        *,
        document: dict[str, Any],
        event: OpenAPIChangeEventWrite,
    ) -> OpenAPIChangeEventRecord:
        """Atomically update current OpenAPI and append its response event."""

        with self.unit_of_work_factory() as uow:
            record = uow.openapi.record_change(
                document=deepcopy(document),
                event=event,
            )
            uow.commit()
            return record

    def list_changes(
        self,
        operation_key: str | None = None,
    ) -> list[OpenAPIChangeEventRecord]:
        """Return chronological audit events, optionally for one operation."""

        with self.unit_of_work_factory() as uow:
            return uow.openapi.list_changes(operation_key)
