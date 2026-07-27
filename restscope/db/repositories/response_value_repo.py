"""SQLAlchemy adapter for persistent response-value monitor pools."""

from __future__ import annotations

from datetime import datetime, timedelta
import math
from uuid import uuid4

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from restscope.agent.api_behavior_monitor.response_value_catalog import (
    PersistedResponseValueSource,
    ResponseValueCatalogRegistration,
    ResponseValueMonitorRecord,
    ResponseValueSource,
)

from ..orm.response_value_orm import (
    ResponseObservationORM,
    ResponseObservationScalarORM,
    ResponseValueMonitorORM,
    ResponseValueORM,
    ResponseValueSourceORM,
)

MAX_RESPONSE_OBSERVATIONS_PER_OPERATION = 100


class SqlAlchemyResponseValueCatalogRepository:
    """
    Define the collaborator contract for sql alchemy response value catalog repository.

    Concrete implementations may vary while callers in the repository and database
    persistence boundary depend only on these declared operations.
    """
    def __init__(self, session: Session) -> None:
        self.session = session

    def ensure_monitor(
        self,
        registration: ResponseValueCatalogRegistration,
        *,
        now: datetime,
    ) -> ResponseValueMonitorRecord:
        """
        Handle ensure monitor as part of the repository and database persistence
        boundary.

        The class owns any required collaborators or state; arguments supply only the
        data needed for this call.
        """
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
        """
        Handle add sources as part of the repository and database persistence boundary.

        The class owns any required collaborators or state; arguments supply only the
        data needed for this call.
        """
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
        """
        Return sources for operation for the repository and database persistence
        boundary.

        The class owns any required collaborators or state; arguments supply only the
        data needed for this call.
        """
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
        """
        Return active monitors for the repository and database persistence boundary.

        The class owns any required collaborators or state; arguments supply only the
        data needed for this call.
        """
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
        """
        Record values for the repository and database persistence boundary.

        The class owns any required collaborators or state; arguments supply only the
        data needed for this call.
        """
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

    def record_observation(
        self,
        *,
        operation_key: str,
        status_code: int,
        media_type: str,
        scalars: list[tuple[str, object]],
        now: datetime,
    ) -> None:
        """
        Record observation for the repository and database persistence boundary.

        The class owns any required collaborators or state; arguments supply only the
        data needed for this call.
        """
        observation_id = f"rvo_{uuid4().hex}"
        self.session.add(
            ResponseObservationORM(
                id=observation_id,
                operation_key=operation_key,
                status_code=status_code,
                media_type=media_type,
                observed_at=now,
            )
        )
        seen: set[tuple[str, str, str]] = set()
        position = 0
        for selector, value in scalars:
            encoded = _encode_value(value)
            if encoded is None:
                continue
            value_type, value_text = encoded
            key = (selector, value_type, value_text)
            if key in seen:
                continue
            seen.add(key)
            self.session.add(
                ResponseObservationScalarORM(
                    id=f"rvsnap_{uuid4().hex}",
                    observation_id=observation_id,
                    selector=selector,
                    position=position,
                    value_type=value_type,
                    value_text=value_text,
                )
            )
            position += 1
        self.session.flush()
        expired_ids = list(
            self.session.scalars(
                select(ResponseObservationORM.id)
                .where(
                    ResponseObservationORM.operation_key == operation_key
                )
                .order_by(
                    ResponseObservationORM.observed_at.desc(),
                    ResponseObservationORM.id.desc(),
                )
                .offset(MAX_RESPONSE_OBSERVATIONS_PER_OPERATION)
            ).all()
        )
        if expired_ids:
            self.session.execute(
                delete(ResponseObservationScalarORM).where(
                    ResponseObservationScalarORM.observation_id.in_(
                        expired_ids
                    )
                )
            )
            self.session.execute(
                delete(ResponseObservationORM).where(
                    ResponseObservationORM.id.in_(expired_ids)
                )
            )
        self.session.flush()

    def historical_values_for_source(
        self,
        source: ResponseValueSource,
        *,
        limit: int,
    ) -> list[object]:
        """
        Handle historical values for source as part of the repository and database
        persistence boundary.

        The class owns any required collaborators or state; arguments supply only the
        data needed for this call.
        """
        rows = self.session.execute(
            select(
                ResponseObservationORM.status_code,
                ResponseObservationScalarORM.value_type,
                ResponseObservationScalarORM.value_text,
            )
            .join(
                ResponseObservationScalarORM,
                ResponseObservationScalarORM.observation_id
                == ResponseObservationORM.id,
            )
            .where(
                ResponseObservationORM.operation_key
                == source.producer_operation_key,
                ResponseObservationORM.media_type == source.media_type,
                ResponseObservationScalarORM.selector == source.selector,
            )
            .order_by(
                ResponseObservationORM.observed_at,
                ResponseObservationORM.id,
                ResponseObservationScalarORM.position,
            )
        ).all()
        values: list[object] = []
        seen: set[tuple[str, str]] = set()
        for status_code, value_type, value_text in rows:
            if not _status_matches(source.status_code, status_code):
                continue
            key = (value_type, value_text)
            if key in seen:
                continue
            seen.add(key)
            values.append(_decode_value(value_type, value_text))
            if len(values) >= limit:
                break
        return values

    def values_for(self, value_name: str, *, limit: int) -> list[object]:
        """
        Handle values for as part of the repository and database persistence boundary.

        The class owns any required collaborators or state; arguments supply only the
        data needed for this call.
        """
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


def _status_matches(declared: str, actual: int) -> bool:
    normalized = declared.upper()
    return normalized == str(actual) or normalized == f"{actual // 100}XX"
