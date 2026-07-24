"""Transactional response-value monitor registration and typed value pools."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from restscope.db.time import utc_now


@dataclass(frozen=True, slots=True)
class ResponseValueCatalogRegistration:
    value_name: str
    consumer_operation_key: str
    consumer_input_node_id: str
    parameter_name: str
    expected_type: str | None


@dataclass(frozen=True, slots=True)
class ResponseValueMonitorRecord:
    monitor_id: str
    value_name: str
    consumer_operation_key: str
    consumer_input_node_id: str
    parameter_name: str
    expected_type: str | None
    active: bool
    created: bool = False


@dataclass(frozen=True, slots=True)
class ResponseValueSource:
    producer_operation_key: str
    status_code: str
    media_type: str
    selector: str
    field_name: str


@dataclass(frozen=True, slots=True)
class PersistedResponseValueSource(ResponseValueSource):
    source_id: str
    monitor_id: str


class _ResponseValueRepository(Protocol):
    def ensure_monitor(
        self,
        registration: ResponseValueCatalogRegistration,
        *,
        now: datetime,
    ) -> ResponseValueMonitorRecord: ...

    def add_sources(
        self,
        monitor_id: str,
        sources: list[ResponseValueSource],
        *,
        now: datetime,
    ) -> list[PersistedResponseValueSource]: ...

    def list_sources_for_operation(
        self,
        producer_operation_key: str,
    ) -> list[PersistedResponseValueSource]: ...

    def list_active_monitors(self) -> list[ResponseValueMonitorRecord]: ...

    def record_values(
        self,
        monitor_id: str,
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
        with self.unit_of_work_factory() as uow:
            result = uow.response_values.ensure_monitor(
                registration,
                now=utc_now(),
            )
            uow.commit()
            return result

    def add_sources(
        self,
        monitor_id: str,
        sources: list[ResponseValueSource],
    ) -> list[PersistedResponseValueSource]:
        with self.unit_of_work_factory() as uow:
            result = uow.response_values.add_sources(
                monitor_id,
                sources,
                now=utc_now(),
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
        with self.unit_of_work_factory() as uow:
            now = utc_now()
            monitor = uow.response_values.ensure_monitor(
                registration,
                now=now,
            )
            persisted = uow.response_values.add_sources(
                monitor.monitor_id,
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
                monitor.monitor_id,
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
        with self.unit_of_work_factory() as uow:
            return uow.response_values.list_sources_for_operation(
                producer_operation_key
            )

    def list_active_monitors(self) -> list[ResponseValueMonitorRecord]:
        with self.unit_of_work_factory() as uow:
            return uow.response_values.list_active_monitors()

    def record_values(
        self,
        monitor_id: str,
        values: list[object],
    ) -> int:
        with self.unit_of_work_factory() as uow:
            count = uow.response_values.record_values(
                monitor_id,
                values,
                now=utc_now(),
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
        with self.unit_of_work_factory() as uow:
            uow.response_values.record_observation(
                operation_key=operation_key,
                status_code=status_code,
                media_type=media_type,
                scalars=scalars,
                now=utc_now(),
            )
            uow.commit()

    def historical_values_for_source(
        self,
        source: ResponseValueSource,
        *,
        limit: int = 100,
    ) -> list[object]:
        with self.unit_of_work_factory() as uow:
            return uow.response_values.historical_values_for_source(
                source,
                limit=limit,
            )

    def values_for(self, value_name: str, *, limit: int = 100) -> list[object]:
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
