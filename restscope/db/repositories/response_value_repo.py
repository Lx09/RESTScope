"""SQLAlchemy adapter for bounded response observations and typed value pools.

The response workflow extracts selectors before calling this adapter.  One
write transaction stores the bounded observation, refreshes every registered
pool, and prunes old rows so a partial response cannot become durable evidence.
"""

from __future__ import annotations

from datetime import datetime, timedelta
import math
from uuid import uuid4

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from restscope.api_behavior_monitor.response_value_catalog import (
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
MAX_RESPONSE_VALUES_PER_POOL = 100
MAX_RESPONSE_SCALARS = 1000


class SqlAlchemyResponseValueCatalogRepository:
    """Persist response evidence within fixed per-operation and per-pool bounds."""

    def __init__(self, session: Session) -> None:
        """Use the transaction and lifecycle owned by the surrounding unit of work."""

        self.session = session

    def ensure_monitor(
        self,
        registration: ResponseValueCatalogRegistration,
        *,
        now: datetime,
    ) -> ResponseValueMonitorRecord:
        """Create or refresh the one pool assigned to a consumer input."""

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
                value_name=registration.value_name,
                consumer_operation_key=registration.consumer_operation_key,
                consumer_input_node_id=registration.consumer_input_node_id,
                parameter_name=registration.parameter_name,
                expected_type=registration.expected_type,
                created_at=now,
                updated_at=now,
            )
            self.session.add(row)
        else:
            if row.value_name != registration.value_name:
                raise ValueError("A consumer input cannot change its response value name")
            row.parameter_name = registration.parameter_name
            row.expected_type = registration.expected_type
            row.updated_at = now
        self.session.flush()
        return _monitor_record(row, created=created)

    def add_sources(
        self,
        value_name: str,
        sources: list[ResponseValueSource],
        *,
        now: datetime,
    ) -> list[PersistedResponseValueSource]:
        """Insert missing explicit producer selectors for one value pool."""

        if self.session.get(ResponseValueMonitorORM, value_name) is None:
            raise ValueError(f"Unknown response-value pool: {value_name}")
        for source in sources:
            key = (
                value_name,
                source.producer_operation_key,
                source.status_code,
                source.media_type,
                source.selector,
            )
            if self.session.get(ResponseValueSourceORM, key) is None:
                self.session.add(
                    ResponseValueSourceORM(
                        value_name=value_name,
                        producer_operation_key=source.producer_operation_key,
                        status_code=source.status_code,
                        media_type=source.media_type,
                        selector=source.selector,
                        field_name=source.field_name,
                        created_at=now,
                    )
                )
        self.session.flush()
        return self._list_sources(value_name=value_name)

    def list_sources_for_operation(
        self,
        producer_operation_key: str,
    ) -> list[PersistedResponseValueSource]:
        """Return every registered selector that reads one producer operation."""

        rows = self.session.scalars(
            select(ResponseValueSourceORM)
            .where(
                ResponseValueSourceORM.producer_operation_key
                == producer_operation_key
            )
            .order_by(
                ResponseValueSourceORM.value_name,
                ResponseValueSourceORM.status_code,
                ResponseValueSourceORM.media_type,
                ResponseValueSourceORM.selector,
            )
        ).all()
        return [_source_record(row) for row in rows]

    def list_monitors(self) -> list[ResponseValueMonitorRecord]:
        """Return all registered pools; every stored monitor is active."""

        rows = self.session.scalars(
            select(ResponseValueMonitorORM).order_by(
                ResponseValueMonitorORM.consumer_operation_key,
                ResponseValueMonitorORM.consumer_input_node_id,
            )
        ).all()
        return [_monitor_record(row, created=False) for row in rows]

    def record_values(
        self,
        value_name: str,
        values: list[object],
        *,
        now: datetime,
    ) -> int:
        """Refresh one pool and prune it to its 100 most recently active values."""

        recorded = self._record_values(value_name, values, now=now)
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
        """Store one bounded observation without updating any registered pool."""

        self._record_observation(
            operation_key=operation_key,
            status_code=status_code,
            media_type=media_type,
            scalars=scalars,
            now=now,
        )
        self.session.flush()

    def record_response(
        self,
        *,
        operation_key: str,
        status_code: int,
        media_type: str,
        scalars: list[tuple[str, object]],
        values_by_pool: dict[str, list[object]],
        now: datetime,
    ) -> int:
        """Store an observation and all pool updates in the active transaction."""

        self._record_observation(
            operation_key=operation_key,
            status_code=status_code,
            media_type=media_type,
            scalars=scalars,
            now=now,
        )
        recorded = 0
        for offset, (value_name, values) in enumerate(sorted(values_by_pool.items())):
            recorded += self._record_values(
                value_name,
                values,
                now=now + timedelta(microseconds=offset),
            )
        self.session.flush()
        return recorded

    def historical_values_for_source(
        self,
        source: ResponseValueSource,
        *,
        limit: int,
    ) -> list[object]:
        """Read distinct historical selector values for registration backfill."""

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
        """Return the most recently active typed values for one registered pool."""

        if self.session.get(ResponseValueMonitorORM, value_name) is None:
            return []
        rows = self.session.scalars(
            select(ResponseValueORM)
            .where(ResponseValueORM.value_name == value_name)
            .order_by(
                ResponseValueORM.last_seen_at,
                ResponseValueORM.first_seen_at,
                ResponseValueORM.value_type,
                ResponseValueORM.value_text,
            )
            .limit(limit)
        ).all()
        return [_decode_value(row.value_type, row.value_text) for row in rows]

    def _record_values(
        self,
        value_name: str,
        values: list[object],
        *,
        now: datetime,
    ) -> int:
        """Upsert typed values and delete rows beyond the pool retention limit."""

        if self.session.get(ResponseValueMonitorORM, value_name) is None:
            raise ValueError(f"Unknown response-value pool: {value_name}")
        recorded = 0
        seen: set[tuple[str, str]] = set()
        for sequence, value in enumerate(values):
            encoded = _encode_value(value)
            if encoded is None or encoded in seen:
                continue
            seen.add(encoded)
            value_type, value_text = encoded
            row = self.session.get(
                ResponseValueORM,
                (value_name, value_type, value_text),
            )
            if row is None:
                timestamp = now + timedelta(microseconds=sequence)
                self.session.add(
                    ResponseValueORM(
                        value_name=value_name,
                        value_type=value_type,
                        value_text=value_text,
                        first_seen_at=timestamp,
                        last_seen_at=timestamp,
                    )
                )
                recorded += 1
            else:
                row.last_seen_at = now + timedelta(microseconds=sequence)
        self.session.flush()
        expired = self.session.scalars(
            select(ResponseValueORM)
            .where(ResponseValueORM.value_name == value_name)
            .order_by(
                ResponseValueORM.last_seen_at.desc(),
                ResponseValueORM.first_seen_at.desc(),
                ResponseValueORM.value_type,
                ResponseValueORM.value_text,
            )
            .offset(MAX_RESPONSE_VALUES_PER_POOL)
        ).all()
        for row in expired:
            self.session.delete(row)
        return recorded

    def _record_observation(
        self,
        *,
        operation_key: str,
        status_code: int,
        media_type: str,
        scalars: list[tuple[str, object]],
        now: datetime,
    ) -> None:
        """Insert distinct scalars, then prune old parent observations."""

        encoded_scalars: list[tuple[str, str, str]] = []
        seen: set[tuple[str, str, str]] = set()
        for selector, value in scalars:
            encoded = _encode_value(value)
            if encoded is None:
                continue
            key = (selector, encoded[0], encoded[1])
            if key not in seen:
                seen.add(key)
                encoded_scalars.append(key)
        if len(encoded_scalars) > MAX_RESPONSE_SCALARS:
            raise ValueError("A response observation cannot exceed 1000 scalar values")

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
        self.session.add_all(
            [
                ResponseObservationScalarORM(
                    observation_id=observation_id,
                    selector=selector,
                    value_type=value_type,
                    value_text=value_text,
                    position=position,
                )
                for position, (selector, value_type, value_text) in enumerate(
                    encoded_scalars
                )
            ]
        )
        self.session.flush()
        expired_ids = list(
            self.session.scalars(
                select(ResponseObservationORM.id)
                .where(ResponseObservationORM.operation_key == operation_key)
                .order_by(
                    ResponseObservationORM.observed_at.desc(),
                    ResponseObservationORM.id.desc(),
                )
                .offset(MAX_RESPONSE_OBSERVATIONS_PER_OPERATION)
            ).all()
        )
        if expired_ids:
            self.session.execute(
                delete(ResponseObservationORM).where(
                    ResponseObservationORM.id.in_(expired_ids)
                )
            )

    def _list_sources(self, *, value_name: str) -> list[PersistedResponseValueSource]:
        """Return one pool's sources in deterministic display order."""

        rows = self.session.scalars(
            select(ResponseValueSourceORM)
            .where(ResponseValueSourceORM.value_name == value_name)
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
    """Project one monitor row into the database-independent contract."""

    return ResponseValueMonitorRecord(
        value_name=row.value_name,
        consumer_operation_key=row.consumer_operation_key,
        consumer_input_node_id=row.consumer_input_node_id,
        parameter_name=row.parameter_name,
        expected_type=row.expected_type,
        created=created,
    )


def _source_record(row: ResponseValueSourceORM) -> PersistedResponseValueSource:
    """Project a natural-key source row into the catalog contract."""

    return PersistedResponseValueSource(
        value_name=row.value_name,
        producer_operation_key=row.producer_operation_key,
        status_code=row.status_code,
        media_type=row.media_type,
        selector=row.selector,
        field_name=row.field_name,
    )


def _encode_value(value: object) -> tuple[str, str] | None:
    """Encode supported scalar values without conflating bool and integer."""

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
    """Decode one stored scalar type used by generators and lookups."""

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
    """Match an exact or OpenAPI wildcard response status declaration."""

    normalized = declared.upper()
    return normalized == str(actual) or normalized == f"{actual // 100}XX"
