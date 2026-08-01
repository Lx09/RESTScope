"""Transactional response-value monitor registration and typed value pools."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol


@dataclass(frozen=True, slots=True)
class ResponseValueCatalogRegistration:
    """
    Coordinate response value catalog registration behavior for API response monitoring
    and its narrowly approved evidence catalog.

    Read the public methods as the supported lifecycle and treat underscore-prefixed
    helpers as internal implementation details.
    """
    value_name: str
    consumer_operation_key: str
    consumer_input_node_id: str
    parameter_name: str
    expected_type: str | None


@dataclass(frozen=True, slots=True)
class ResponseValueMonitorRecord:
    """
    Carry validated response value monitor record data across API response monitoring
    and its narrowly approved evidence catalog.

    The annotated fields form the contract; validation rejects missing, extra, or
    incorrectly typed values at the boundary.
    """
    value_name: str
    consumer_operation_key: str
    consumer_input_node_id: str
    parameter_name: str
    expected_type: str | None
    created: bool = False


@dataclass(frozen=True, slots=True)
class ResponseValueSource:
    """
    Carry validated response value source data across API response monitoring and its
    narrowly approved evidence catalog.

    The annotated fields form the contract; validation rejects missing, extra, or
    incorrectly typed values at the boundary.
    """
    producer_operation_key: str
    status_code: str
    media_type: str
    selector: str
    field_name: str


@dataclass(frozen=True, slots=True)
class PersistedResponseValueSource(ResponseValueSource):
    """
    Carry validated persisted response value source data across API response monitoring
    and its narrowly approved evidence catalog.

    The annotated fields form the contract; validation rejects missing, extra, or
    incorrectly typed values at the boundary.
    """
    value_name: str


class _ResponseValueRepository(Protocol):
    """
    Define the collaborator contract for response value repository.

    Concrete implementations may vary while callers in API response monitoring and its
    narrowly approved evidence catalog depend only on these declared operations.
    """
    def ensure_monitor(
        self,
        registration: ResponseValueCatalogRegistration,
        *,
        now: datetime,
    ) -> ResponseValueMonitorRecord: ...

    def add_sources(
        self,
        value_name: str,
        sources: list[ResponseValueSource],
        *,
        now: datetime,
    ) -> list[PersistedResponseValueSource]: ...

    def list_sources_for_operation(
        self,
        producer_operation_key: str,
    ) -> list[PersistedResponseValueSource]: ...

    def list_monitors(self) -> list[ResponseValueMonitorRecord]: ...

    def record_values(
        self,
        value_name: str,
        values: list[object],
        *,
        now: datetime,
    ) -> int: ...

    def values_for(self, value_name: str, *, limit: int) -> list[object]: ...

    def record_observation(
        self,
        *,
        operation_key: str,
        status_code: int,
        media_type: str,
        scalars: list[tuple[str, object]],
        now: datetime,
    ) -> None: ...

    def record_response(
        self,
        *,
        operation_key: str,
        status_code: int,
        media_type: str,
        scalars: list[tuple[str, object]],
        values_by_pool: dict[str, list[object]],
        now: datetime,
    ) -> int: ...

    def historical_values_for_source(
        self,
        source: ResponseValueSource,
        *,
        limit: int,
    ) -> list[object]: ...


class _ResponseValueUnitOfWork(Protocol):
    response_values: _ResponseValueRepository

    def __enter__(self) -> "_ResponseValueUnitOfWork": ...

    def __exit__(self, exc_type, exc, tb) -> None: ...

    def commit(self) -> None: ...


class ResponseValueCatalog:
    """Persist only registered selectors and deduplicated typed scalar values."""

    def __init__(self, unit_of_work_factory) -> None:
        self.unit_of_work_factory = unit_of_work_factory

    def ensure_monitor(
        self,
        registration: ResponseValueCatalogRegistration,
    ) -> ResponseValueMonitorRecord:
        """
        Handle ensure monitor as part of API response monitoring and its narrowly
        approved evidence catalog.

        The class owns any required collaborators or state; arguments supply only the
        data needed for this call.
        """
        with self.unit_of_work_factory() as uow:
            result = uow.response_values.ensure_monitor(
                registration,
                now=_utc_now(),
            )
            uow.commit()
            return result

    def record_response(
        self,
        *,
        operation_key: str,
        status_code: int,
        media_type: str,
        scalars: list[tuple[str, object]],
        values_by_pool: dict[str, list[object]],
    ) -> int:
        """Atomically store one observation and refresh every matching pool.

        ``values_by_pool`` is computed by the response workflow before opening
        the write transaction, so the SQL adapter never interprets selectors or
        raw JSON response shapes.
        """

        with self.unit_of_work_factory() as uow:
            recorded = uow.response_values.record_response(
                operation_key=operation_key,
                status_code=status_code,
                media_type=media_type,
                scalars=scalars,
                values_by_pool=values_by_pool,
                now=_utc_now(),
            )
            uow.commit()
            return recorded

    def add_sources(
        self,
        value_name: str,
        sources: list[ResponseValueSource],
    ) -> list[PersistedResponseValueSource]:
        """
        Handle add sources as part of API response monitoring and its narrowly approved
        evidence catalog.

        The class owns any required collaborators or state; arguments supply only the
        data needed for this call.
        """
        with self.unit_of_work_factory() as uow:
            result = uow.response_values.add_sources(
                value_name,
                sources,
                now=_utc_now(),
            )
            uow.commit()
            return result

    def register_with_backfill(
        self,
        registration: ResponseValueCatalogRegistration,
        sources: list[ResponseValueSource],
    ) -> tuple[
        ResponseValueMonitorRecord,
        list[PersistedResponseValueSource],
    ]:
        """
        Handle register with backfill as part of API response monitoring and its
        narrowly approved evidence catalog.

        The class owns any required collaborators or state; arguments supply only the
        data needed for this call.
        """
        with self.unit_of_work_factory() as uow:
            now = _utc_now()
            monitor = uow.response_values.ensure_monitor(
                registration,
                now=now,
            )
            persisted = uow.response_values.add_sources(
                monitor.value_name,
                sources,
                now=now,
            )
            historical_values: list[object] = []
            for source in sources:
                historical_values.extend(
                    uow.response_values.historical_values_for_source(
                        source,
                        limit=100,
                    )
                )
            historical_values = [
                value
                for value in historical_values
                if _value_matches_expected_type(
                    registration.expected_type,
                    value,
                )
            ]
            if not historical_values:
                raise ValueError(
                    "Selected response sources have no compatible values"
                )
            uow.response_values.record_values(
                monitor.value_name,
                historical_values,
                now=now,
            )
            if not uow.response_values.values_for(
                monitor.value_name,
                limit=1,
            ):
                raise ValueError(
                    "Response-value registration produced an empty pool"
                )
            uow.commit()
            return monitor, persisted

    def list_sources_for_operation(
        self,
        producer_operation_key: str,
    ) -> list[PersistedResponseValueSource]:
        """
        Return sources for operation for API response monitoring and its narrowly
        approved evidence catalog.

        The class owns any required collaborators or state; arguments supply only the
        data needed for this call.
        """
        with self.unit_of_work_factory() as uow:
            return uow.response_values.list_sources_for_operation(
                producer_operation_key
            )

    def list_monitors(self) -> list[ResponseValueMonitorRecord]:
        """
        Return active monitors for API response monitoring and its narrowly approved
        evidence catalog.

        The class owns any required collaborators or state; arguments supply only the
        data needed for this call.
        """
        with self.unit_of_work_factory() as uow:
            return uow.response_values.list_monitors()

    def record_values(
        self,
        value_name: str,
        values: list[object],
    ) -> int:
        """
        Record values for API response monitoring and its narrowly approved evidence
        catalog.

        The class owns any required collaborators or state; arguments supply only the
        data needed for this call.
        """
        with self.unit_of_work_factory() as uow:
            count = uow.response_values.record_values(
                value_name,
                values,
                now=_utc_now(),
            )
            uow.commit()
            return count

    def record_observation(
        self,
        *,
        operation_key: str,
        status_code: int,
        media_type: str,
        scalars: list[tuple[str, object]],
    ) -> None:
        """
        Record observation for API response monitoring and its narrowly approved
        evidence catalog.

        The class owns any required collaborators or state; arguments supply only the
        data needed for this call.
        """
        with self.unit_of_work_factory() as uow:
            uow.response_values.record_observation(
                operation_key=operation_key,
                status_code=status_code,
                media_type=media_type,
                scalars=scalars,
                now=_utc_now(),
            )
            uow.commit()

    def historical_values_for_source(
        self,
        source: ResponseValueSource,
        *,
        limit: int = 100,
    ) -> list[object]:
        """
        Handle historical values for source as part of API response monitoring and its
        narrowly approved evidence catalog.

        The class owns any required collaborators or state; arguments supply only the
        data needed for this call.
        """
        with self.unit_of_work_factory() as uow:
            return uow.response_values.historical_values_for_source(
                source,
                limit=limit,
            )

    def values_for(self, value_name: str, *, limit: int = 100) -> list[object]:
        """
        Handle values for as part of API response monitoring and its narrowly approved
        evidence catalog.

        The class owns any required collaborators or state; arguments supply only the
        data needed for this call.
        """
        with self.unit_of_work_factory() as uow:
            return uow.response_values.values_for(value_name, limit=limit)


def _value_matches_expected_type(
    expected_type: str | None,
    value: object,
) -> bool:
    if expected_type is None:
        return True
    if expected_type == "string":
        return isinstance(value, str)
    if expected_type == "boolean":
        return isinstance(value, bool)
    if expected_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected_type == "number":
        return (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
        )
    return True


def _utc_now() -> datetime:
    """Return an aware UTC timestamp without importing the database package."""

    return datetime.now(timezone.utc)
