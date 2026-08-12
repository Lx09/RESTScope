"""Resolve exact response sources without maintaining shared value pools.

Parameter Patch validation and Batch generation use this Module to turn an
``OperationInputSourceReference`` into current values.  Generic VALUE_REUSE
sources are parsed on demand from retained observations.  RESOURCE sources
read complete current resource instances so composite identities remain
correlated.  Applying a Patch stores only the source relationship and its
neutral Beta prior; it never stores a copied producer-value collection.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
import json

from restscope.api_behavior_monitor.catalog import (
    OperationDefinition,
    OperationInputSource,
    ResourceDefinitionRecord,
    APIBehaviorCatalog,
)
from restscope.target_http.request import normalize_media_type
from restscope.openapi_parser import OpenAPISpecIR
from restscope.operation_references import ResponseFieldReference

from .models import (
    InputNodeSnapshot,
    OperationGeneratorConfig,
    OperationInputSourceReference,
    ParameterSnapshot,
    ResourceIdentifierGenerator,
    ResponseValueGenerator,
)
from .parameter_patch.models import SelectedReferenceProvenance
from .store import ReferenceValueBinding


_SCALAR_REFERENCE_TYPES = frozenset({"string", "integer", "number", "boolean"})
_MAX_RESPONSE_VALUE_CANDIDATES = 8


class BehaviorMonitorReferenceValues:
    """Adapt the API Behavior Catalog to Generator value reads."""

    def __init__(self, catalog: APIBehaviorCatalog) -> None:
        """Bind the durable response facts used by later request generation."""

        self.catalog = catalog

    def values_for(
        self,
        strategy: ResourceIdentifierGenerator | ResponseValueGenerator,
    ) -> Sequence[object]:
        """Return current typed values for one exact Generator source.

        Response values retain newest-observation order and are de-duplicated
        by both JSON scalar type and value.  Resource values are projected from
        complete instance states and therefore use the Catalog's current merge.
        """

        if isinstance(strategy, ResourceIdentifierGenerator):
            component = strategy.source.field_name
            return tuple(
                record[component]
                for record in self.resource_records(strategy)
                if component in record
                and _is_json_scalar(record[component])
            )

        source = strategy.source
        reference = ResponseFieldReference.from_selector(source.selector)
        values: list[object] = []
        offset = 0
        while len(values) < _MAX_RESPONSE_VALUE_CANDIDATES and offset < 100:
            observations = self.catalog.list_observations(
                operation_id=source.producer_operation_id,
                status_code=source.status_code,
                media_type=source.media_type,
                offset=offset,
                limit=_MAX_RESPONSE_VALUE_CANDIDATES,
            )
            if not observations:
                break
            for observation in observations:
                body = json.loads(observation.response_json)
                values = _deduplicate_values(
                    [*values, *reference.select_values(body)]
                )[:_MAX_RESPONSE_VALUE_CANDIDATES]
                if len(values) == _MAX_RESPONSE_VALUE_CANDIDATES:
                    break
            offset += len(observations)
        return tuple(values)

    def resource_key(self, strategy: ResourceIdentifierGenerator) -> str:
        """Return the unique resource type that owns the selected identity field."""

        return self._resource_for(strategy.source).name

    def resource_records(
        self,
        strategy: ResourceIdentifierGenerator,
    ) -> Sequence[Mapping[str, object]]:
        """Return all non-deleted current instances for one exact resource source."""

        resource = self._resource_for(strategy.source)
        output: list[Mapping[str, object]] = []
        offset = 0
        while True:
            page, total = self.catalog.list_resource_instances(
                resource_type=resource.name,
                offset=offset,
                limit=200,
            )
            output.extend(item.current_state_json for item in page)
            offset += len(page)
            if offset >= total or not page:
                return tuple(output)

    def resource_identity_fields(
        self,
        strategy: ResourceIdentifierGenerator,
    ) -> Sequence[str]:
        """Return immutable identity fields used to validate composite bindings."""

        return self._resource_for(strategy.source).identity_fields

    def _resource_for(
        self,
        source: OperationInputSourceReference,
    ) -> ResourceDefinitionRecord:
        """Resolve one producer identity field or reject an ambiguous source."""

        candidates = [
            resource
            for resource in self.catalog.list_operation_resources(
                operation_id=source.producer_operation_id,
            )
            if source.field_name in resource.identity_fields
        ]
        if len(candidates) != 1:
            raise ValueError(
                "Resource source must identify exactly one producer resource"
            )
        return candidates[0]

    def resolve_response_source(
        self,
        *,
        config: OperationGeneratorConfig,
        input_node_id: str,
        operation_key: str,
        status_code: int,
        media_type: str,
        field: str,
        ir: OpenAPISpecIR | None = None,
    ) -> tuple[SelectedReferenceProvenance, list[object]]:
        """Validate one model-selected response field against current facts.

        The status must be an actual successful status, not an OpenAPI range or
        ``default`` response.  The optional IR check prevents applying a field
        that disappeared after the Agent selected it.
        """

        reference = ResponseFieldReference.from_handle(field)
        if not reference.property_names:
            raise ValueError("Response source must identify a property")
        normalized_media_type = normalize_media_type(media_type)
        if normalized_media_type is None:
            raise ValueError("Response source media type cannot be blank")
        if ir is not None and not _response_field_exists(
            ir=ir,
            operation_key=operation_key,
            status_code=status_code,
            media_type=normalized_media_type,
            field=field,
        ):
            raise ValueError(
                "The selected response field is no longer in the current OpenAPI IR"
            )
        source = OperationInputSourceReference(
            producer_operation_id=operation_key,
            status_code=status_code,
            media_type=normalized_media_type,
            selector=reference.selector,
            field_name=reference.property_names[-1],
        )
        nodes = {item.input_node_id: item for item in config.snapshot.input_nodes}
        parameters = {
            item.input_node_id: item for item in config.snapshot.parameters
        }
        node = nodes.get(input_node_id)
        if node is None:
            raise ValueError(f"Unknown response-value input node: {input_node_id}")
        compatible, expected_type = _reference_expected_type(
            node=node,
            parameter=parameters.get(input_node_id),
        )
        if not compatible:
            raise ValueError("Response values can only target scalar inputs")
        strategy = ResponseValueGenerator(type="response_value", source=source)
        values = [
            value
            for value in self.values_for(strategy)
            if _observed_value_compatible(expected_type, value)
        ]
        if not values:
            raise ValueError("The selected response field has no compatible values")
        return (
            SelectedReferenceProvenance(
                input_node_id=input_node_id,
                kind="response_value",
                source=source,
                compatible_scalar_type=(
                    expected_type or "|".join(_observed_scalar_types(values))
                ),
                value_count=len(values),
            ),
            values,
        )

    @contextmanager
    def stage_bindings(
        self,
        *,
        config: OperationGeneratorConfig,
        bindings: Sequence[ReferenceValueBinding],
    ) -> Iterator[None]:
        """Stage exact input-source rows in the Patch publication transaction.

        The database transaction stays open while the caller publishes the new
        in-memory Generation State.  A database commit failure then propagates
        through the Store's replacement transaction, which restores the prior
        in-memory revision.
        """

        operations = [_operation_definition(config.operation_key)]
        operations.extend(
            _operation_definition(operation_id)
            for operation_id in sorted(
                {item.producer_operation_id for item in bindings}
            )
            if operation_id != config.operation_key
        )
        sources = [
            OperationInputSource(
                consumer_operation_id=config.operation_key,
                consumer_input_node_id=binding.input_node_id,
                producer_operation_id=binding.producer_operation_id,
                status_code=binding.status_code,
                media_type=binding.media_type,
                selector=binding.selector,
                field_name=binding.field_name,
                consume_type=(
                    "RESOURCE"
                    if binding.kind == "resource_identifier"
                    else "VALUE_REUSE"
                ),
            )
            for binding in bindings
        ]
        with self.catalog.stage_input_sources(
            operations=operations,
            sources=sources,
        ):
            yield


def _operation_definition(operation_id: str) -> OperationDefinition:
    """Split the normalized operation identity used throughout RESTScope."""

    method, separator, path = operation_id.partition(" ")
    if not separator or not method or not path:
        raise ValueError("operation ID must contain method and path")
    return OperationDefinition(
        operation_id=operation_id,
        method=method,
        path=path,
    )


def _is_json_scalar(value: object) -> bool:
    """Return whether a value can safely feed a scalar request input."""

    return isinstance(value, (str, int, float, bool)) and value is not None


def _reference_expected_type(
    *,
    node: InputNodeSnapshot,
    parameter: ParameterSnapshot | None,
) -> tuple[bool, str | None]:
    """Describe scalar compatibility for an observed response value."""

    if node.schema_contract is None:
        return False, None
    declared_type = _expected_type(node.schema_contract.type)
    if declared_type not in _SCALAR_REFERENCE_TYPES:
        return False, None
    # OpenAPI parameters stringify scalars; JSON request-body fields retain type.
    return True, None if parameter is not None else declared_type


def _expected_type(value: str | list[str] | None) -> str | None:
    """Collapse one nullable OpenAPI type declaration to its scalar type."""

    if isinstance(value, str):
        return value
    if isinstance(value, list):
        concrete = [item for item in value if item != "null"]
        return concrete[0] if len(concrete) == 1 else None
    return None


def _observed_value_compatible(expected_type: str | None, value: object) -> bool:
    """Apply exact JSON scalar typing to one historical response value."""

    if expected_type is None:
        return _is_json_scalar(value)
    if expected_type == "string":
        return isinstance(value, str)
    if expected_type == "boolean":
        return isinstance(value, bool)
    if expected_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected_type == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return False


def _deduplicate_values(values: list[object]) -> list[object]:
    """Preserve observation order while distinguishing Boolean and integer values."""

    output: list[object] = []
    seen: set[tuple[type[object], object]] = set()
    for value in values:
        if not _is_json_scalar(value):
            continue
        key = (type(value), value)
        if key in seen:
            continue
        seen.add(key)
        output.append(value)
    return output


def _observed_scalar_types(values: list[object]) -> list[str]:
    """Return stable JSON scalar type names represented by current values."""

    names: set[str] = set()
    for value in values:
        if isinstance(value, bool):
            names.add("boolean")
        elif isinstance(value, int):
            names.add("integer")
        elif isinstance(value, float):
            names.add("number")
        elif isinstance(value, str):
            names.add("string")
    return sorted(names)


def _response_field_exists(
    *,
    ir: OpenAPISpecIR,
    operation_key: str,
    status_code: int,
    media_type: str,
    field: str,
) -> bool:
    """Require one exact scalar handle in the selected current response contract."""

    operation = ir.operations.get(operation_key)
    if operation is None:
        return False
    response = (
        operation.responses.by_status.get(str(status_code))
        or operation.responses.by_status.get(f"{status_code // 100}XX")
        or operation.responses.by_status.get("default")
    )
    if response is None:
        return False
    schema = next(
        (
            content.schema
            for declared_media, content in response.contents.items()
            if normalize_media_type(declared_media) == media_type
            and content.schema is not None
        ),
        None,
    )
    if schema is None:
        return False
    return field in _response_scalar_handles(schema)


def _response_scalar_handles(
    schema: object,
    *,
    reference: ResponseFieldReference | None = None,
    visited: set[int] | None = None,
) -> set[str]:
    """Enumerate readable scalar handles including arrays and Schema branches."""

    reference = reference or ResponseFieldReference.body()
    visited = set() if visited is None else set(visited)
    if id(schema) in visited or schema.write_only is True:
        return set()
    visited.add(id(schema))
    output: set[str] = set()
    for name, child in schema.properties.items():
        output.update(
            _response_scalar_handles(
                child,
                reference=reference.property(name),
                visited=visited,
            )
        )
    if schema.items is not None:
        output.update(
            _response_scalar_handles(
                schema.items,
                reference=reference.items(),
                visited=visited,
            )
        )
    for kind, branches in (
        ("allOf", schema.all_of),
        ("oneOf", schema.one_of),
        ("anyOf", schema.any_of),
    ):
        for index, branch in enumerate(branches):
            output.update(
                _response_scalar_handles(
                    branch,
                    reference=reference.variant(kind, index),
                    visited=visited,
                )
            )
    if not output and _expected_type(schema.type) in _SCALAR_REFERENCE_TYPES:
        output.add(reference.handle)
    return output
