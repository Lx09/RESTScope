"""Persist the complete API Behavior Monitor evidence and audit catalog.

The Catalog stores the current normalized OpenAPI document, append-only
Contract changes, successful observations, derived resources, exact request
input sources, and immutable abstract Test Case snapshots. Callers use this one
Interface while concrete SQL statements remain in :mod:`restscope.db`.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime
import json
import re
from types import TracebackType
from typing import Literal, Protocol, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from restscope.data_types import JSONObject
from restscope.target_api.media_type import normalize_media_type
from restscope.operation_references.response import ResponseSourceCoordinate


class _CatalogModel(BaseModel):
    """Reject unknown fields and prevent persisted records changing in memory."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class OpenAPIChangeEventWrite(_CatalogModel):
    """Describe one validated response Contract change before insertion."""

    operation_key: str = Field(min_length=1)
    status_code: int = Field(ge=100, le=599)
    media_type: str | None = None
    changes: list[str] = Field(min_length=1)
    response_before: dict[str, object] | None = None
    response_after: dict[str, object]


class OpenAPIChangeEventRecord(OpenAPIChangeEventWrite):
    """Return one persisted Contract change through the Catalog Interface."""

    id: str
    created_at: datetime


class OperationDefinition(_CatalogModel):
    """Describe one normalized OpenAPI operation used by runtime monitoring."""

    operation_id: str = Field(min_length=1, max_length=2000)
    method: str = Field(min_length=1, max_length=20)
    path: str = Field(min_length=1, max_length=2000)
    description: str | None = None

    @field_validator("method")
    @classmethod
    def normalize_method(cls, value: str) -> str:
        """Use the uppercase method spelling shared by the OpenAPI IR."""

        return value.strip().upper()

    @model_validator(mode="after")
    def require_normalized_identity(self) -> "OperationDefinition":
        """Prevent a row identity from disagreeing with its method and path."""

        if self.operation_id != f"{self.method} {self.path}":
            raise ValueError("operation_id must equal the normalized method and path")
        return self


class ObservationWrite(_CatalogModel):
    """Describe one complete successful JSON exchange before insertion.

    ``response_json`` remains the exact decoded text supplied by the transport.
    Validation parses it only to prove that later selector resolution can read
    it; the text is never replaced with a reformatted representation.
    """

    operation_id: str = Field(min_length=1, max_length=2000)
    timestamp: datetime
    status_code: int = Field(ge=200, le=299)
    media_type: str = Field(min_length=1, max_length=500)
    request_json: JSONObject
    response_json: str
    abstract_test_case_id: str | None = None

    @field_validator("media_type")
    @classmethod
    def normalize_media_type_value(cls, value: str) -> str:
        """Store a lowercase media type without transport parameters."""

        normalized = normalize_media_type(value)
        if normalized is None:
            raise ValueError("media_type cannot be blank")
        return normalized

    @field_validator("response_json")
    @classmethod
    def require_valid_json(cls, value: str) -> str:
        """Reject invalid JSON while preserving every original character."""

        json.loads(value)
        return value


class ObservationRecord(ObservationWrite):
    """Return one stored observation with its generated durable identity."""

    observation_id: str


class ObservedResponseCoordinate(_CatalogModel):
    """Identify one successful response coordinate with retained observations."""

    operation_key: str
    status_code: int = Field(ge=200, le=299)
    media_type: str


def normalize_resource_name(value: str) -> str:
    """Return the established lowercase alphanumeric resource identity.

    The previous Resource Catalog called this value ``normalized_name``.  The
    redesigned ``resources.name`` column stores that same identity directly so
    callers never have to choose between canonical and normalized spellings.

    Raises:
        ValueError: The supplied name has no letters or digits.
    """

    normalized = re.sub(r"[^a-z0-9]+", "", value.casefold())
    if not normalized:
        raise ValueError("resource name has no identifier characters")
    return normalized


class ResourceDerivation(_CatalogModel):
    """Describe one resource type and its instances found in one response."""

    resource_name: str = Field(min_length=1, max_length=200)
    identity_fields: list[str] = Field(min_length=1, max_length=20)
    role: str = Field(min_length=1, max_length=100)
    instances: list[JSONObject]

    @field_validator("resource_name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        """Store only the unique normalized resource name."""

        return normalize_resource_name(value)

    @field_validator("identity_fields")
    @classmethod
    def normalize_identity_fields(cls, values: list[str]) -> list[str]:
        """Sort direct identity fields and reject ambiguous duplicates."""

        normalized = [value.strip() for value in values]
        if any(not value for value in normalized):
            raise ValueError("identity fields cannot be blank")
        if "_deleted" in normalized:
            raise ValueError("_deleted is reserved for resource lifecycle state")
        if len(normalized) != len(set(normalized)):
            raise ValueError("identity fields must be unique")
        return sorted(normalized)

    @field_validator("role")
    @classmethod
    def normalize_role(cls, value: str) -> str:
        """Use stable uppercase role names while allowing future vocabulary."""

        normalized = value.strip().upper()
        if not normalized:
            raise ValueError("role cannot be blank")
        return normalized

    @model_validator(mode="after")
    def require_complete_instance_identities(self) -> "ResourceDerivation":
        """Require every candidate object to carry the complete typed identity."""

        for instance in self.instances:
            for field_name in self.identity_fields:
                value = instance.get(field_name)
                if isinstance(value, bool) or not isinstance(value, (str, int)):
                    raise ValueError(
                        "resource identity values must be strings or integers"
                    )
        return self


class ResourceDefinitionRecord(_CatalogModel):
    """Return one stored resource type through Catalog and Tool reads."""

    resource_id: str
    name: str
    identity_fields: tuple[str, ...]


class ResourceInstanceRecord(_CatalogModel):
    """Return one resource instance with its complete current merged state."""

    resource_type: str
    resource_instance_id: str
    current_state_json: JSONObject


class ResourceDerivationResult(_CatalogModel):
    """Summarize one atomic resource update without exposing ORM details."""

    resources: tuple[ResourceDefinitionRecord, ...] = ()
    instances: tuple[ResourceInstanceRecord, ...] = ()
    conflicts: tuple[str, ...] = ()


class OperationInputSource(ResponseSourceCoordinate):
    """Identify one exact producer field selected for one consumer input.

    ``alpha`` and ``beta`` expose the stored Beta prior only to deterministic
    application code.  No Tool or Generator uses them to select behavior in
    this version, and no update operation exists.
    """

    consumer_operation_id: str = Field(min_length=1, max_length=2000)
    consumer_input_node_id: str = Field(min_length=1, max_length=2000)
    consume_type: Literal["RESOURCE", "VALUE_REUSE"]
    alpha: int = Field(default=1, ge=1)
    beta: int = Field(default=1, ge=1)


class AbstractTestCaseWrite(_CatalogModel):
    """Describe one immutable operation Generation State audit snapshot."""

    operation_id: str = Field(min_length=1, max_length=2000)
    state_digest: str = Field(min_length=1, max_length=64)
    generators_json: JSONObject
    constraints_json: JSONObject


class AbstractTestCaseRecord(AbstractTestCaseWrite):
    """Return one stored abstract test-case identity and creation time."""

    abstract_test_case_id: str
    created_at: datetime


def resource_instance_id(
    identity_fields: tuple[str, ...] | list[str],
    instance: JSONObject,
) -> str:
    """Build one type-preserving canonical composite resource identity."""

    identity = {field_name: instance[field_name] for field_name in identity_fields}
    return json.dumps(
        identity,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def merge_resource_state(
    previous: JSONObject | None,
    observed: JSONObject,
    *,
    deleted: bool,
) -> JSONObject:
    """Recursively merge one observed object into current resource state.

    Missing keys retain their old values.  A null object field never removes or
    overwrites an old value, while arrays are factual values and therefore
    replace the previous array as a whole, including any null elements inside
    them.  The reserved lifecycle marker is always written last so target data
    cannot impersonate Monitor state.
    """

    output = _merge_json_objects(previous or {}, observed)
    output["_deleted"] = deleted
    return output


def _merge_json_objects(previous: JSONObject, observed: JSONObject) -> JSONObject:
    """Return a detached recursive object merge used by resource state updates."""

    output: JSONObject = deepcopy(previous)
    for key, new_value in observed.items():
        if new_value is None:
            continue
        old_value = output.get(key)
        if isinstance(old_value, dict) and isinstance(new_value, dict):
            output[key] = _merge_json_objects(old_value, new_value)
        elif isinstance(new_value, dict):
            output[key] = _merge_json_objects({}, new_value)
        else:
            output[key] = deepcopy(new_value)
    return output


class _APIBehaviorRepository(Protocol):
    """Define persistence operations required by the public Catalog."""

    def initialize_api(
        self,
        *,
        document: dict[str, object],
        operations: list[OperationDefinition],
    ) -> None: ...

    def get_current_openapi(self) -> dict[str, object] | None: ...

    def record_openapi_change(
        self,
        *,
        document: dict[str, object],
        event: OpenAPIChangeEventWrite,
    ) -> OpenAPIChangeEventRecord: ...

    def list_openapi_changes(
        self,
        operation_key: str | None = None,
    ) -> list[OpenAPIChangeEventRecord]: ...

    def ensure_operation(
        self,
        operation: OperationDefinition,
    ) -> OperationDefinition: ...

    def record_observation(
        self,
        observation: ObservationWrite,
    ) -> ObservationRecord: ...

    def list_observations(
        self,
        *,
        operation_id: str,
        status_code: int | None,
        media_type: str | None,
        offset: int,
        limit: int,
    ) -> list[ObservationRecord]: ...

    def list_observed_response_coordinates(
        self,
    ) -> list[ObservedResponseCoordinate]: ...

    def record_resource_derivations(
        self,
        *,
        operation_id: str,
        derivations: list[ResourceDerivation],
    ) -> ResourceDerivationResult: ...

    def list_resources(
        self,
        *,
        offset: int,
        limit: int,
    ) -> tuple[list[ResourceDefinitionRecord], int]: ...

    def get_resource(self, *, name: str) -> ResourceDefinitionRecord | None: ...

    def list_resource_instances(
        self,
        *,
        resource_type: str,
        offset: int,
        limit: int,
        include_deleted: bool,
    ) -> tuple[list[ResourceInstanceRecord], int]: ...

    def list_operation_resources(
        self,
        *,
        operation_id: str,
    ) -> list[ResourceDefinitionRecord]: ...

    def ensure_input_source(
        self,
        source: OperationInputSource,
    ) -> OperationInputSource: ...

    def list_input_sources(
        self,
        *,
        consumer_operation_id: str,
        consumer_input_node_id: str,
    ) -> list[OperationInputSource]: ...

    def ensure_abstract_test_case(
        self,
        test_case: AbstractTestCaseWrite,
    ) -> AbstractTestCaseRecord: ...


class _APIBehaviorUnitOfWork(Protocol):
    """Expose one API Behavior repository inside one database transaction."""

    api_behavior: _APIBehaviorRepository

    def __enter__(self) -> "_APIBehaviorUnitOfWork": ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None: ...

    def commit(self) -> None: ...


_APIBehaviorUnitOfWorkFactory: TypeAlias = Callable[[], _APIBehaviorUnitOfWork]


class APIBehaviorCatalog:
    """Provide one transaction Interface for all durable API Behavior state."""

    def __init__(
        self,
        unit_of_work_factory: _APIBehaviorUnitOfWorkFactory,
    ) -> None:
        """Retain the factory without opening a database connection."""

        self._unit_of_work_factory = unit_of_work_factory

    def initialize_api(
        self,
        *,
        document: dict[str, object],
        operations: list[OperationDefinition],
    ) -> None:
        """Atomically insert one normalized document and its operation metadata.

        Reinitialization raises ``ValueError`` without changing the existing
        document or adding any supplied operation. The caller retains ownership
        of its mapping; persistence receives an isolated copy.
        """

        with self._unit_of_work_factory() as uow:
            uow.api_behavior.initialize_api(
                document=deepcopy(document),
                operations=operations,
            )
            uow.commit()

    def current_openapi(self) -> dict[str, object]:
        """Return an isolated copy of the current normalized OpenAPI document."""

        with self._unit_of_work_factory() as uow:
            document = uow.api_behavior.get_current_openapi()
        if document is None:
            raise RuntimeError("The API Behavior Catalog has not been initialized")
        return deepcopy(document)

    def record_openapi_change(
        self,
        *,
        document: dict[str, object],
        event: OpenAPIChangeEventWrite,
    ) -> OpenAPIChangeEventRecord:
        """Atomically replace current OpenAPI and append one Contract change."""

        with self._unit_of_work_factory() as uow:
            record = uow.api_behavior.record_openapi_change(
                document=deepcopy(document),
                event=event,
            )
            uow.commit()
            return record

    def list_openapi_changes(
        self,
        operation_key: str | None = None,
    ) -> list[OpenAPIChangeEventRecord]:
        """Return chronological Contract changes, optionally for one operation."""

        with self._unit_of_work_factory() as uow:
            return uow.api_behavior.list_openapi_changes(operation_key)

    def ensure_operation(
        self,
        operation: OperationDefinition,
    ) -> OperationDefinition:
        """Insert or refresh one operation's current descriptive metadata."""

        with self._unit_of_work_factory() as uow:
            result = uow.api_behavior.ensure_operation(operation)
            uow.commit()
            return result

    def record_observation(
        self,
        observation: ObservationWrite,
    ) -> ObservationRecord:
        """Insert one observation and atomically retain the latest one hundred."""

        with self._unit_of_work_factory() as uow:
            result = uow.api_behavior.record_observation(observation)
            uow.commit()
            return result

    def list_observations(
        self,
        *,
        operation_id: str,
        status_code: int | None = None,
        media_type: str | None = None,
        offset: int = 0,
        limit: int = 100,
    ) -> list[ObservationRecord]:
        """Return newest observations first for one exact operation.

        Args:
            operation_id: Normalized operation key whose facts are requested.
            status_code: Optional exact successful response status.
            media_type: Optional exact media type after normalization.
            offset: Number of matching newest-first rows to skip.
            limit: Maximum rows to return. The database retention contract
                makes values above one hundred unnecessary and invalid.

        Returns:
            Immutable records ordered by response time and observation ID.

        Raises:
            ValueError: ``operation_id`` is blank or ``limit`` is outside
                ``1..100``.
        """

        if not operation_id.strip():
            raise ValueError("operation_id cannot be blank")
        if status_code is not None and not 200 <= status_code <= 299:
            raise ValueError("status_code must be a successful response status")
        normalized_media_type = None
        if media_type is not None:
            normalized_media_type = normalize_media_type(media_type)
            if normalized_media_type is None:
                raise ValueError("media_type cannot be blank")
        if offset < 0:
            raise ValueError("offset cannot be negative")
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        with self._unit_of_work_factory() as uow:
            return uow.api_behavior.list_observations(
                operation_id=operation_id,
                status_code=status_code,
                media_type=normalized_media_type,
                offset=offset,
                limit=limit,
            )

    def list_observed_response_coordinates(
        self,
    ) -> list[ObservedResponseCoordinate]:
        """Return distinct response coordinates without loading response JSON."""

        with self._unit_of_work_factory() as uow:
            return uow.api_behavior.list_observed_response_coordinates()

    def record_resource_derivations(
        self,
        *,
        operation_id: str,
        derivations: list[ResourceDerivation],
    ) -> ResourceDerivationResult:
        """Atomically write valid resources, edges, and merged instances.

        Identity conflicts are reported by the repository and skipped per
        resource.  Unrelated derivations in the same response still commit.
        """

        if not operation_id.strip():
            raise ValueError("operation_id cannot be blank")
        with self._unit_of_work_factory() as uow:
            result = uow.api_behavior.record_resource_derivations(
                operation_id=operation_id,
                derivations=derivations,
            )
            uow.commit()
            return result

    def list_resources(
        self,
        *,
        offset: int,
        limit: int,
    ) -> tuple[list[ResourceDefinitionRecord], int]:
        """Return one alphabetical resource page for the Resource Tool."""

        _require_page(offset=offset, limit=limit)
        with self._unit_of_work_factory() as uow:
            return uow.api_behavior.list_resources(offset=offset, limit=limit)

    def get_resource(self, name: str) -> ResourceDefinitionRecord | None:
        """Return one exact normalized resource without scanning list pages."""

        normalized_name = normalize_resource_name(name)
        if normalized_name != name:
            return None
        with self._unit_of_work_factory() as uow:
            return uow.api_behavior.get_resource(name=normalized_name)

    def list_resource_instances(
        self,
        *,
        resource_type: str,
        offset: int,
        limit: int,
        include_deleted: bool = False,
    ) -> tuple[list[ResourceInstanceRecord], int]:
        """Return one stable page of exact-name resource instances."""

        normalized_type = normalize_resource_name(resource_type)
        _require_page(offset=offset, limit=limit)
        with self._unit_of_work_factory() as uow:
            return uow.api_behavior.list_resource_instances(
                resource_type=normalized_type,
                offset=offset,
                limit=limit,
                include_deleted=include_deleted,
            )

    def list_operation_resources(
        self,
        *,
        operation_id: str,
    ) -> list[ResourceDefinitionRecord]:
        """Return resource types connected to one producer operation.

        The result intentionally omits edge roles: source resolution only asks
        which resource owns an identity field.  A field that matches more than
        one returned resource remains ambiguous and is rejected by Request
        Generation instead of being guessed.
        """

        if not operation_id.strip():
            raise ValueError("operation_id cannot be blank")
        with self._unit_of_work_factory() as uow:
            return uow.api_behavior.list_operation_resources(
                operation_id=operation_id,
            )

    def ensure_input_source(
        self,
        source: OperationInputSource,
    ) -> OperationInputSource:
        """Insert or reuse one exact source without changing its Beta prior."""

        with self._unit_of_work_factory() as uow:
            result = uow.api_behavior.ensure_input_source(source)
            uow.commit()
            return result

    def list_input_sources(
        self,
        *,
        consumer_operation_id: str,
        consumer_input_node_id: str,
    ) -> list[OperationInputSource]:
        """Return every historical source registered for one consumer input."""

        if not consumer_operation_id.strip() or not consumer_input_node_id.strip():
            raise ValueError("consumer operation and input node cannot be blank")
        with self._unit_of_work_factory() as uow:
            return uow.api_behavior.list_input_sources(
                consumer_operation_id=consumer_operation_id,
                consumer_input_node_id=consumer_input_node_id,
            )

    def ensure_abstract_test_case(
        self,
        test_case: AbstractTestCaseWrite,
    ) -> AbstractTestCaseRecord:
        """Insert or reuse one exact operation and state-digest snapshot."""

        with self._unit_of_work_factory() as uow:
            result = uow.api_behavior.ensure_abstract_test_case(test_case)
            uow.commit()
            return result

    @contextmanager
    def stage_input_sources(
        self,
        *,
        operations: list[OperationDefinition],
        sources: list[OperationInputSource],
    ) -> Iterator[None]:
        """Stage exact operation/source rows around a caller-owned publication.

        The database transaction remains open while the caller publishes its
        in-memory Generation State. Normal context exit commits the rows. Any
        exception from publication or commit rolls the transaction back and is
        allowed to reach the Store replacement context, which restores its prior
        revision before releasing the operation lock.
        """

        with self._unit_of_work_factory() as uow:
            for operation in operations:
                uow.api_behavior.ensure_operation(operation)
            for source in sources:
                uow.api_behavior.ensure_input_source(source)
            yield
            uow.commit()


def _require_page(*, offset: int, limit: int) -> None:
    """Validate the shared bounded Resource Tool pagination contract."""

    if offset < 0:
        raise ValueError("offset cannot be negative")
    if not 1 <= limit <= 200:
        raise ValueError("limit must be between 1 and 200")


__all__ = [
    "AbstractTestCaseRecord",
    "AbstractTestCaseWrite",
    "ObservationRecord",
    "ObservationWrite",
    "ObservedResponseCoordinate",
    "OpenAPIChangeEventRecord",
    "OpenAPIChangeEventWrite",
    "OperationDefinition",
    "OperationInputSource",
    "ResourceDefinitionRecord",
    "ResourceDerivation",
    "ResourceDerivationResult",
    "ResourceInstanceRecord",
    "APIBehaviorCatalog",
]
