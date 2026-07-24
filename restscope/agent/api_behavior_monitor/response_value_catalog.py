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

    def values_for(self, value_name: str, *, limit: int = 100) -> list[object]:
        with self.unit_of_work_factory() as uow:
            return uow.response_values.values_for(value_name, limit=limit)
