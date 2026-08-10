"""Transactional response-value monitor registration and typed value pools."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Protocol


@dataclass(frozen=True, slots=True)
class ResponseValueCatalogRegistration:
    """Return the monitor and sources created or reused by one registration transaction."""
    value_name: str
    consumer_operation_key: str
    consumer_input_node_id: str
    parameter_name: str
    expected_type: str | None


@dataclass(frozen=True, slots=True)
class ResponseValueMonitorRecord:
    """Identify one registered request input and the response sources allowed to supply its values."""
    value_name: str
    consumer_operation_key: str
    consumer_input_node_id: str
    parameter_name: str
    expected_type: str | None
    created: bool = False


@dataclass(frozen=True, slots=True)
class ResponseValueSource:
    """Identify one operation, response status, media type, and field selector that produces typed values."""
    producer_operation_key: str
    status_code: str
    media_type: str
    selector: str
    field_name: str


@dataclass(frozen=True, slots=True)
class PersistedResponseValueSource(ResponseValueSource):
    """Add the database identity required to update one validated response-value source."""
    value_name: str


@dataclass(frozen=True, slots=True)
class ObservedResponseField:
    """Identify one scalar selector that appeared in a retained response.

    This read model contains no scalar value, timestamp, observation database
    key, or response-value pool name. The concrete HTTP status is preserved so
    OpenAPI lookup can apply its existing exact, class, and default matching.
    """

    operation_key: str
    status_code: int
    media_type: str
    selector: str


class _ResponseValueRepository(Protocol):
    """Define the exact monitor, source, observation, and typed-value persistence operations required by ResponseValueCatalog."""
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

    def list_observed_response_fields(self) -> list[ObservedResponseField]: ...

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
        """Return the existing monitor for a response field or create it atomically."""
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
        """Attach new validated producers to an existing response-value monitor."""
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
        """Create a monitor, add its sources, and backfill values in one transaction."""
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

    def register_many_with_backfill(
        self,
        registrations: list[
            tuple[ResponseValueCatalogRegistration, list[ResponseValueSource]]
        ],
    ) -> list[
        tuple[ResponseValueMonitorRecord, list[PersistedResponseValueSource]]
    ]:
        """Register several consumer pools in one all-or-nothing transaction.

        Parameter Patch uses this Interface so a multi-input Patch cannot leave
        only some response-value sources durable when a later source has lost
        its compatible historical values.
        """
        with self.unit_of_work_factory() as uow:
            now = _utc_now()
            results: list[
                tuple[ResponseValueMonitorRecord, list[PersistedResponseValueSource]]
            ] = []
            for offset, (registration, sources) in enumerate(registrations):
                item_now = now + timedelta(microseconds=offset)
                monitor = uow.response_values.ensure_monitor(
                    registration,
                    now=item_now,
                )
                persisted = uow.response_values.add_sources(
                    monitor.value_name,
                    sources,
                    now=item_now,
                )
                historical_values: list[object] = []
                for source in sources:
                    historical_values.extend(
                        uow.response_values.historical_values_for_source(
                            source,
                            limit=100,
                        )
                    )
                compatible = [
                    value
                    for value in historical_values
                    if _value_matches_expected_type(
                        registration.expected_type,
                        value,
                    )
                ]
                if not compatible:
                    raise ValueError(
                        "Selected response sources have no compatible values"
                    )
                uow.response_values.record_values(
                    monitor.value_name,
                    compatible,
                    now=item_now,
                )
                if not uow.response_values.values_for(monitor.value_name, limit=1):
                    raise ValueError(
                        "Response-value registration produced an empty pool"
                    )
                results.append((monitor, persisted))
            uow.commit()
            return results

    def list_sources_for_operation(
        self,
        producer_operation_key: str,
    ) -> list[PersistedResponseValueSource]:
        """
        Return sources for operation for API response monitoring and its narrowly
        approved evidence catalog.
        """
        with self.unit_of_work_factory() as uow:
            return uow.response_values.list_sources_for_operation(
                producer_operation_key
            )

    def list_monitors(self) -> list[ResponseValueMonitorRecord]:
        """List every active response-value monitor with its currently registered sources."""
        with self.unit_of_work_factory() as uow:
            return uow.response_values.list_monitors()

    def list_observed_response_fields(self) -> list[ObservedResponseField]:
        """Return distinct retained scalar field identities without values.

        The SQL Adapter performs the distinct projection, so repeated responses
        cannot multiply the OpenAPI lookup candidate list. The Catalog does not
        interpret selectors or decide whether they still exist in the current
        OpenAPI IR; that belongs to the lookup Capability.
        """
        with self.unit_of_work_factory() as uow:
            return uow.response_values.list_observed_response_fields()

    def record_values(
        self,
        value_name: str,
        values: list[object],
    ) -> int:
        """Store distinct typed values for one validated source without retaining the raw response."""
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
        """Store a bounded scalar observation index used for later source backfill."""
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
        """Return distinct typed values previously observed for one exact producer."""
        with self.unit_of_work_factory() as uow:
            return uow.response_values.historical_values_for_source(
                source,
                limit=limit,
            )

    def values_for(self, value_name: str, *, limit: int = 100) -> list[object]:
        """Return the bounded current value pool for one registered monitor."""
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
