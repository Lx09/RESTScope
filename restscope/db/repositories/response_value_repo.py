"""SQLAlchemy adapter for persistent response-value monitor pools."""

from __future__ import annotations

from datetime import datetime, timedelta
import math
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from restscope.agent.api_behavior_monitor.response_value_catalog import (
    PersistedResponseValueSource,
    ResponseValueCatalogRegistration,
    ResponseValueMonitorRecord,
    ResponseValueSource,
)

from ..orm.response_value_orm import (
    ResponseValueMonitorORM,
    ResponseValueORM,
    ResponseValueSourceORM,
)


class SqlAlchemyResponseValueCatalogRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def ensure_monitor(
        self,
        registration: ResponseValueCatalogRegistration,
        *,
        now: datetime,
    ) -> ResponseValueMonitorRecord:
        row = self.session.scalar(
            select(ResponseValueMonitorORM).where(
                ResponseValueMonitorORM.consumer_operation_key
                == registration.consumer_operation_key,
                ResponseValueMonitorORM.consumer_input_node_id
                == registration.consumer_input_node_id,
            )
        )
        created = row is None
        if row is None:
            row = ResponseValueMonitorORM(
                id=f"rvm_{uuid4().hex}",
                value_name=registration.value_name,
                consumer_operation_key=registration.consumer_operation_key,
                consumer_input_node_id=registration.consumer_input_node_id,
                parameter_name=registration.parameter_name,
                expected_type=registration.expected_type,
                active=True,
                created_at=now,
                updated_at=now,
            )
            self.session.add(row)
        else:
            row.parameter_name = registration.parameter_name
            row.expected_type = registration.expected_type
            row.active = True
            row.updated_at = now
        self.session.flush()
        return _monitor_record(row, created=created)

    def add_sources(
        self,
        monitor_id: str,
        sources: list[ResponseValueSource],
        *,
        now: datetime,
    ) -> list[PersistedResponseValueSource]:
        if self.session.get(ResponseValueMonitorORM, monitor_id) is None:
            raise ValueError(f"Unknown response-value monitor: {monitor_id}")
        for source in sources:
            row = self.session.scalar(
                select(ResponseValueSourceORM).where(
                    ResponseValueSourceORM.monitor_id == monitor_id,
                    ResponseValueSourceORM.producer_operation_key
                    == source.producer_operation_key,
                    ResponseValueSourceORM.status_code == source.status_code,
                    ResponseValueSourceORM.media_type == source.media_type,
                    ResponseValueSourceORM.selector == source.selector,
                )
            )
            if row is None:
                self.session.add(
                    ResponseValueSourceORM(
                        id=f"rvs_{uuid4().hex}",
                        monitor_id=monitor_id,
                        producer_operation_key=source.producer_operation_key,
                        status_code=source.status_code,
                        media_type=source.media_type,
                        selector=source.selector,
                        field_name=source.field_name,
                        created_at=now,
                    )
                )
        self.session.flush()
        return self._list_sources(monitor_id=monitor_id)

    def list_sources_for_operation(
        self,
        producer_operation_key: str,
    ) -> list[PersistedResponseValueSource]:
        rows = self.session.scalars(
            select(ResponseValueSourceORM)
            .join(
                ResponseValueMonitorORM,
                ResponseValueMonitorORM.id == ResponseValueSourceORM.monitor_id,
            )
            .where(
                ResponseValueSourceORM.producer_operation_key
                == producer_operation_key,
                ResponseValueMonitorORM.active.is_(True),
            )
            .order_by(
                ResponseValueSourceORM.monitor_id,
                ResponseValueSourceORM.status_code,
                ResponseValueSourceORM.media_type,
                ResponseValueSourceORM.selector,
            )
        ).all()
        return [_source_record(row) for row in rows]

    def list_active_monitors(self) -> list[ResponseValueMonitorRecord]:
        rows = self.session.scalars(
            select(ResponseValueMonitorORM)
            .where(ResponseValueMonitorORM.active.is_(True))
            .order_by(
                ResponseValueMonitorORM.consumer_operation_key,
                ResponseValueMonitorORM.consumer_input_node_id,
            )
        ).all()
        return [_monitor_record(row, created=False) for row in rows]

    def record_values(
        self,
        monitor_id: str,
        values: list[object],
        *,
        now: datetime,
    ) -> int:
        if self.session.get(ResponseValueMonitorORM, monitor_id) is None:
            raise ValueError(f"Unknown response-value monitor: {monitor_id}")
        recorded = 0
        seen: set[tuple[str, str]] = set()
        for value in values:
            encoded = _encode_value(value)
            if encoded is None or encoded in seen:
                continue
            seen.add(encoded)
            value_type, value_text = encoded
            row = self.session.scalar(
                select(ResponseValueORM).where(
                    ResponseValueORM.monitor_id == monitor_id,
                    ResponseValueORM.value_type == value_type,
                    ResponseValueORM.value_text == value_text,
                )
            )
            if row is None:
                first_seen_at = now + timedelta(microseconds=recorded)
                self.session.add(
                    ResponseValueORM(
                        id=f"rv_{uuid4().hex}",
                        monitor_id=monitor_id,
                        value_type=value_type,
                        value_text=value_text,
                        first_seen_at=first_seen_at,
                        last_seen_at=first_seen_at,
                    )
                )
                recorded += 1
            else:
                row.last_seen_at = now
        self.session.flush()
        return recorded

    def values_for(self, value_name: str, *, limit: int) -> list[object]:
        monitor = self.session.scalar(
            select(ResponseValueMonitorORM).where(
                ResponseValueMonitorORM.value_name == value_name,
                ResponseValueMonitorORM.active.is_(True),
            )
        )
        if monitor is None:
            return []
        rows = self.session.scalars(
            select(ResponseValueORM)
            .where(ResponseValueORM.monitor_id == monitor.id)
            .order_by(ResponseValueORM.first_seen_at, ResponseValueORM.id)
            .limit(limit)
        ).all()
        return [_decode_value(row.value_type, row.value_text) for row in rows]

    def _list_sources(
        self,
        *,
        monitor_id: str,
    ) -> list[PersistedResponseValueSource]:
        rows = self.session.scalars(
            select(ResponseValueSourceORM)
            .where(ResponseValueSourceORM.monitor_id == monitor_id)
            .order_by(
                ResponseValueSourceORM.producer_operation_key,
                ResponseValueSourceORM.status_code,
                ResponseValueSourceORM.media_type,
                ResponseValueSourceORM.selector,
            )
        ).all()
        return [_source_record(row) for row in rows]


def _monitor_record(
    row: ResponseValueMonitorORM,
    *,
    created: bool,
) -> ResponseValueMonitorRecord:
    return ResponseValueMonitorRecord(
        monitor_id=row.id,
        value_name=row.value_name,
        consumer_operation_key=row.consumer_operation_key,
        consumer_input_node_id=row.consumer_input_node_id,
        parameter_name=row.parameter_name,
        expected_type=row.expected_type,
        active=row.active,
        created=created,
    )


def _source_record(row: ResponseValueSourceORM) -> PersistedResponseValueSource:
    return PersistedResponseValueSource(
        source_id=row.id,
        monitor_id=row.monitor_id,
        producer_operation_key=row.producer_operation_key,
        status_code=row.status_code,
        media_type=row.media_type,
        selector=row.selector,
        field_name=row.field_name,
    )


def _encode_value(value: object) -> tuple[str, str] | None:
    if isinstance(value, str):
        return ("string", value)
    if isinstance(value, bool):
        return ("boolean", "true" if value else "false")
    if isinstance(value, int):
        return ("integer", str(value))
    if isinstance(value, float) and math.isfinite(value):
        return ("number", repr(value))
    return None


def _decode_value(value_type: str, value_text: str) -> object:
    if value_type == "string":
        return value_text
    if value_type == "boolean":
        return value_text == "true"
    if value_type == "integer":
        return int(value_text)
    if value_type == "number":
        return float(value_text)
    raise ValueError(f"Unsupported response value type: {value_type}")
