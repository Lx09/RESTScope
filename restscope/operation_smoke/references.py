"""Adapters from API behavior evidence to reference-backed generators."""

from __future__ import annotations

from restscope.api_behavior_monitor import (
    APIBehaviorMonitorCoordinator,
    ResourceLookupRequest,
    ResponseValueSource,
)
from restscope.api_behavior_monitor.contract_tracker import normalize_media_type
from restscope.operation_smoke.parameter_patch import SelectedReferenceProvenance
from restscope.openapi_parser import OpenAPISpecIR
from restscope.response_fields import ResponseFieldReference
from restscope.testing import (
    InputGeneratorPatch,
    InputNodeSnapshot,
    OperationGeneratorConfig,
    ParameterSnapshot,
    ResourceIdentifierGenerator,
    ResponseValueGenerator,
)

_SCALAR_REFERENCE_TYPES = frozenset(
    {"string", "integer", "number", "boolean"}
)


class BehaviorMonitorReferenceValues:
    """Resolve both resource identifiers and generic monitored response values."""

    def __init__(self, coordinator: APIBehaviorMonitorCoordinator) -> None:
        """Bind the monitor whose App-lifetime catalogs supply reference values."""
        self.coordinator = coordinator

    def values_for(
        self,
        strategy: ResourceIdentifierGenerator | ResponseValueGenerator,
    ) -> list[object]:
        """Load the typed values used by one reference-backed Generator.

        Resource-identifier Generators read the resource catalog. Generic
        response-value Generators read their named monitored value pool. An
        empty list means the Generator cannot currently produce a request.
        """
        if isinstance(strategy, ResponseValueGenerator):
            return self.coordinator.response_values_for(strategy.value_name)
        result = self.coordinator.lookup(
            ResourceLookupRequest(
                resource=strategy.resource,
                limit=100,
            )
        )
        return [item.value for item in result.identifiers]

    def resolve_response_source(
        self,
        *,
        config: OperationGeneratorConfig,
        input_node_id: str,
        operation_key: str,
        matched_status_code: str,
        media_type: str,
        field: str,
        ir: OpenAPISpecIR | None = None,
    ) -> tuple[SelectedReferenceProvenance, list[object]]:
        """Re-read one exact producer field for candidate-only Patch sampling.

        Args:
            config: Current consumer operation Generator configuration.
            input_node_id: Target request input selected by the Patch model.
            operation_key: Producer operation copied from the lookup result.
            matched_status_code: Exact, status-class, or default OpenAPI
                response contract copied from the lookup result.
            media_type: Normalized producer response media type.
            field: Model-facing ``body...`` producer field handle.
            ir: Optional current OpenAPI document used by Apply to reject a
                source whose response contract changed after proposal.

        Returns:
            Internal provenance and the current compatible historical scalars.
            No response-value monitor or consumer pool is created.

        Raises:
            ValueError: The consumer is not scalar, the field handle is invalid,
                or the retained compatible pool is empty.
        """
        nodes = {
            item.input_node_id: item for item in config.snapshot.input_nodes
        }
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
        reference = ResponseFieldReference.from_handle(field)
        if ir is not None and not _response_field_exists(
            ir=ir,
            operation_key=operation_key,
            status_code=matched_status_code,
            media_type=media_type,
            field=field,
        ):
            raise ValueError(
                "The selected response field is no longer in the current OpenAPI IR"
            )
        if not reference.property_names:
            field_name = "body"
        else:
            field_name = reference.property_names[-1]
        observation_media_type = normalize_media_type(media_type)
        if observation_media_type is None:
            raise ValueError("Response Value source media type is empty")
        source = ResponseValueSource(
            producer_operation_key=operation_key,
            status_code=matched_status_code,
            media_type=observation_media_type,
            selector=reference.selector,
            field_name=field_name,
        )
        preview = self.coordinator.preview_selected_response_value_source(
            consumer_operation_key=config.operation_key,
            consumer_input_node_id=input_node_id,
            expected_type=expected_type,
            source=source,
        )
        if preview is None:
            raise ValueError(
                "The selected response field has no current compatible values"
            )
        values = [
            value
            for value in (
                self.coordinator.response_value_tracker.catalog
                .historical_values_for_source(source, limit=100)
            )
            if _observed_value_compatible(expected_type, value)
        ]
        values = _deduplicate_values(values)
        if not values:
            raise ValueError(
                "The selected response field has no current compatible values"
            )
        return (
            SelectedReferenceProvenance(
                input_node_id=input_node_id,
                kind="response_value",
                value_name=preview.value_name,
                compatible_scalar_type=(
                    expected_type or "|".join(_observed_scalar_types(values))
                ),
                value_count=len(values),
                producer_operation_keys=[operation_key],
                producer_status_code=matched_status_code,
                producer_media_type=media_type,
                source_field=field,
                source_selector=reference.selector,
            ),
            values,
        )

    def prepare_updates(
        self,
        *,
        ir: OpenAPISpecIR,
        config: OperationGeneratorConfig,
        updates: list[InputGeneratorPatch],
        selected_reference_provenance: list[SelectedReferenceProvenance] | None = None,
    ) -> list[InputGeneratorPatch]:
        """Revalidate selected response provenance and register it at Apply."""

        nodes = {
            item.input_node_id: item
            for item in config.snapshot.input_nodes
        }
        parameters = {
            item.input_node_id: item
            for item in config.snapshot.parameters
        }
        selected_by_input = {
            item.input_node_id: item
            for item in (selected_reference_provenance or [])
        }
        prepared: list[InputGeneratorPatch] = []
        for update in updates:
            strategy = update.strategy
            selected = selected_by_input.get(update.input_node_id)
            if isinstance(strategy, ResourceIdentifierGenerator):
                if (
                    selected is None
                    or selected.kind != "resource_identifier"
                    or selected.canonical_resource != strategy.resource
                ):
                    raise ValueError(
                        "Resource Identifier Generator requires selected "
                        "canonical-resource provenance"
                    )
                node = nodes.get(update.input_node_id)
                if node is None:
                    raise ValueError(
                        f"Unknown resource input node: {update.input_node_id}"
                    )
                compatible, expected_type = _reference_expected_type(
                    node=node,
                    parameter=parameters.get(update.input_node_id),
                )
                values = self.values_for(strategy)
                if (
                    not compatible
                    or not values
                    or not all(
                        _observed_value_compatible(expected_type, value)
                        for value in values
                    )
                ):
                    raise ValueError(
                        "Resource Identifier pool changed or became incompatible"
                    )
                prepared.append(update)
                continue
            if not isinstance(strategy, ResponseValueGenerator):
                prepared.append(update)
                continue
            node = nodes.get(update.input_node_id)
            if node is None:
                raise ValueError(
                    f"Unknown response-value input node: {update.input_node_id}"
                )
            parameter = parameters.get(update.input_node_id)
            parameter_name = _input_name(node=node, parameter=parameter)
            compatible, expected_type = _reference_expected_type(
                node=node,
                parameter=parameter,
            )
            if not compatible:
                raise ValueError(
                    "Response values can only be assigned to scalar inputs"
                )
            if selected is None or selected.kind != "response_value":
                raise ValueError(
                    "Response Value Generator requires selected producer provenance"
                )
            if selected.value_name != strategy.value_name:
                raise ValueError(
                    "Selected response provenance does not match Generator"
                )
            assert selected.producer_status_code is not None
            assert selected.producer_media_type is not None
            assert selected.source_selector is not None
            assert selected.source_field is not None
            # Revalidate both current IR and retained values immediately before
            # the first persistent response-pool registration.
            self.resolve_response_source(
                config=config,
                input_node_id=update.input_node_id,
                operation_key=selected.producer_operation_keys[0],
                matched_status_code=selected.producer_status_code,
                media_type=selected.producer_media_type,
                field=selected.source_field,
                ir=ir,
            )
            source_reference = ResponseFieldReference.from_handle(
                selected.source_field
            )
            try:
                registration = self.coordinator.register_response_value_sources(
                    consumer_operation_key=config.operation_key,
                    consumer_input_node_id=update.input_node_id,
                    parameter_name=parameter_name,
                    expected_type=expected_type,
                    sources=[
                        ResponseValueSource(
                            producer_operation_key=(
                                selected.producer_operation_keys[0]
                            ),
                            status_code=selected.producer_status_code,
                            media_type=(
                                normalize_media_type(
                                    selected.producer_media_type
                                )
                                or selected.producer_media_type
                            ),
                            selector=selected.source_selector,
                            field_name=(
                                source_reference.property_names[-1]
                                if source_reference.property_names
                                else "body"
                            ),
                        )
                    ],
                )
            except RuntimeError as exc:
                if getattr(exc, "code", None) != "response_value_pool_unavailable":
                    raise
                raise ValueError(
                    "Selected response values disappeared before registration"
                ) from exc
            prepared.append(
                update.model_copy(
                    update={
                        "strategy": ResponseValueGenerator(
                            type="response_value",
                            value_name=registration.value_name,
                        )
                    }
                )
            )
        return prepared


def _input_name(
    *,
    node: InputNodeSnapshot,
    parameter: ParameterSnapshot | None,
) -> str:
    """Return the human-readable input name shown in Patch requirements."""
    if parameter is not None:
        return parameter.name
    return node.canonical_path.rstrip("/").rsplit("/", 1)[-1]


def _reference_expected_type(
    *,
    node: InputNodeSnapshot,
    parameter: ParameterSnapshot | None,
) -> tuple[bool, str | None]:
    """Describe whether observed scalar values are safe for one frozen input.

    The Boolean reports basic compatibility.  The optional type is the exact
    JSON scalar type required when the input is part of a request body.
    """
    if node.schema_contract is None:
        return False, None
    declared_type = _expected_type(node.schema_contract.type)
    if declared_type not in _SCALAR_REFERENCE_TYPES:
        return False, None
    # OpenAPI parameter serialization can safely stringify any observed scalar.
    # Body fields retain their declared scalar type because JSON preserves types.
    return True, None if parameter is not None else declared_type


def _expected_type(value: str | list[str] | None) -> str | None:
    """Collapse one nullable OpenAPI type declaration to its scalar type."""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        concrete = [item for item in value if item != "null"]
        return concrete[0] if len(concrete) == 1 else None
    return None


def _observed_value_compatible(
    expected_type: str | None,
    value: object,
) -> bool:
    """Apply JSON scalar typing to one historical response value."""
    if expected_type is None:
        return isinstance(value, (str, int, float, bool))
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
    """Preserve historical order while removing equal values of equal type."""
    output: list[object] = []
    seen: set[tuple[type, object]] = set()
    for value in values:
        key = (type(value), value)
        if key in seen:
            continue
        seen.add(key)
        output.append(value)
    return output


def _observed_scalar_types(values: list[object]) -> list[str]:
    """Return stable JSON scalar type names represented by preview values."""
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
    status_code: str,
    media_type: str,
    field: str,
) -> bool:
    """Require one exact handle in the selected current response contract."""
    operation = ir.operations.get(operation_key)
    if operation is None:
        return False
    response = operation.responses.by_status.get(status_code)
    if response is None:
        return False
    schema = next(
        (
            content.schema
            for declared_media, content in response.contents.items()
            if normalize_media_type(declared_media)
            == normalize_media_type(media_type)
            and content.schema is not None
        ),
        None,
    )
    if schema is None:
        return False
    return field in _response_scalar_handles(schema)


def _response_scalar_handles(
    schema,
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
