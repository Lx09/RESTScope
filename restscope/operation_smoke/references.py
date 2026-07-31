"""Adapters from API behavior evidence to reference-backed generators."""

from __future__ import annotations

import re
from hashlib import sha256

from restscope.api_behavior_monitor import (
    APIBehaviorMonitorCoordinator,
    ResourceLookupRequest,
    ResponseValueSource,
)
from restscope.operation_smoke.parameter_patch import AvailableReferenceOption
from restscope.openapi_parser import OpenAPISpecIR
from restscope.testing import (
    InputGeneratorPatch,
    InputNodeSnapshot,
    OperationGeneratorConfig,
    ParameterSnapshot,
    ResourceIdentifierGenerator,
    ResponseValueGenerator,
)

_MAX_REFERENCE_OPTIONS = 100
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

    def available_options(
        self,
        *,
        ir: OpenAPISpecIR,
        config: OperationGeneratorConfig,
        input_node_ids: set[str] | None = None,
    ) -> list[AvailableReferenceOption]:
        """Describe only reference generators whose persistent pool is non-empty."""

        nodes = {
            item.input_node_id: item for item in config.snapshot.input_nodes
        }
        parameters = {
            item.input_node_id: item
            for item in config.snapshot.parameters
        }
        options: list[AvailableReferenceOption] = []

        # A response option is consumer-specific, so expose it before the
        # cross-product of resource pools and inputs reaches the hard bound.
        for item in config.configs:
            if (
                input_node_ids is not None
                and item.input_node_id not in input_node_ids
            ):
                continue
            node = nodes.get(item.input_node_id)
            if node is None:
                continue
            parameter = parameters.get(item.input_node_id)
            compatible, expected_type = _reference_expected_type(
                node=node,
                parameter=parameter,
            )
            if not compatible:
                continue
            parameter_name = _input_name(
                node=node,
                parameter=parameter,
            )
            source_options = self.coordinator.available_response_value_sources(
                ir=ir,
                consumer_operation_key=config.operation_key,
                consumer_input_node_id=item.input_node_id,
                parameter_name=parameter_name,
                expected_type=expected_type,
            )
            for source_option in source_options:
                source = source_option.source
                options.append(
                    AvailableReferenceOption(
                        option_id=_option_id(
                            item.input_node_id,
                            "response_value",
                            "\0".join(
                                (
                                    source_option.value_name,
                                    source.producer_operation_key,
                                    source.status_code,
                                    source.media_type,
                                    source.selector,
                                )
                            ),
                        ),
                        input_node_id=item.input_node_id,
                        kind="response_value",
                        value_name=source_option.value_name,
                        compatible_scalar_type=(
                            source_option.compatible_scalar_type
                        ),
                        value_count=source_option.value_count,
                        producer_operation_keys=[
                            source.producer_operation_key
                        ],
                        producer_status_code=source.status_code,
                        producer_media_type=source.media_type,
                        source_field=source.field_name,
                        source_selector=source.selector,
                    )
                )
                if len(options) >= _MAX_REFERENCE_OPTIONS:
                    return options

        resources = self.coordinator.resource_identifier_tracker.catalog.list_resources(
            limit=_MAX_REFERENCE_OPTIONS,
            aliases_per_resource=0,
        )
        populated_resources = []
        for resource in resources:
            lookup = self.coordinator.lookup(
                ResourceLookupRequest(
                    resource=resource.canonical_name,
                    limit=100,
                )
            )
            if lookup.identifiers:
                populated_resources.append((resource, lookup))

        for item in config.configs:
            if (
                input_node_ids is not None
                and item.input_node_id not in input_node_ids
            ):
                continue
            node = nodes.get(item.input_node_id)
            if node is None:
                continue
            compatible, expected_type = _reference_expected_type(
                node=node,
                parameter=parameters.get(item.input_node_id),
            )
            if not compatible:
                continue
            for resource, lookup in populated_resources:
                if not _resource_matches_input(
                    canonical_resource=resource.canonical_name,
                    aliases=lookup.aliases,
                    input_names=_input_names(
                        node=node,
                        nodes=nodes,
                        parameters=parameters,
                    ),
                    operation_path=config.snapshot.path,
                ):
                    continue
                if not _resource_pool_compatible(
                    expected_type,
                    [value.value_type for value in lookup.identifiers],
                ):
                    continue
                options.append(
                    AvailableReferenceOption(
                        option_id=_option_id(
                            item.input_node_id,
                            "resource_identifier",
                            resource.canonical_name,
                        ),
                        input_node_id=item.input_node_id,
                        kind="resource_identifier",
                        canonical_resource=resource.canonical_name,
                        compatible_scalar_type=expected_type,
                        value_count=len(lookup.identifiers),
                        producer_operation_keys=list(
                            dict.fromkeys(
                                operation.operation_key
                                for operation in lookup.operations
                            )
                        ),
                    )
                )
                if len(options) >= _MAX_REFERENCE_OPTIONS:
                    return options
        return options

    def prepare_updates(
        self,
        *,
        ir: OpenAPISpecIR,
        config: OperationGeneratorConfig,
        updates: list[InputGeneratorPatch],
        selected_reference_options: list[AvailableReferenceOption] | None = None,
    ) -> list[InputGeneratorPatch]:
        """Register response pools and replace model-proposed names locally."""

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
            for item in (selected_reference_options or [])
        }
        prepared: list[InputGeneratorPatch] = []
        for update in updates:
            strategy = update.strategy
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
            selected = selected_by_input.get(update.input_node_id)
            if (
                selected is not None
                and selected.kind == "response_value"
            ):
                if selected.value_name != strategy.value_name:
                    raise ValueError(
                        "Selected response option does not match Generator"
                    )
                assert selected.producer_status_code is not None
                assert selected.producer_media_type is not None
                assert selected.source_selector is not None
                assert selected.source_field is not None
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
                            media_type=selected.producer_media_type,
                            selector=selected.source_selector,
                            field_name=selected.source_field,
                        )
                    ],
                )
            else:
                registration = self.coordinator.register_response_value(
                    ir=ir,
                    consumer_operation_key=config.operation_key,
                    consumer_input_node_id=update.input_node_id,
                    parameter_name=parameter_name,
                    expected_type=expected_type,
                )
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


def _input_names(
    *,
    node: InputNodeSnapshot,
    nodes: dict[str, InputNodeSnapshot],
    parameters: dict[str, ParameterSnapshot],
) -> list[str]:
    """Collect names that give one nested input its semantic meaning.

    Args:
        node: The candidate input that may consume an observed identifier.
        nodes: All frozen inputs keyed by their opaque runtime identity.
        parameters: OpenAPI Parameter metadata keyed by input identity.

    Returns:
        The leaf name followed by unique ancestor names, without changing the
        frozen operation snapshot.
    """
    names: list[str] = []
    current: InputNodeSnapshot | None = node
    while current is not None:
        name = _input_name(
            node=current,
            parameter=parameters.get(current.input_node_id),
        )
        if name not in names:
            names.append(name)
        current = (
            nodes.get(current.parent_node_id)
            if current.parent_node_id is not None
            else None
        )
    return names


def _resource_matches_input(
    *,
    canonical_resource: str,
    aliases: list[str],
    input_names: list[str],
    operation_path: str,
) -> bool:
    """Allow identifier pools only where names identify the same resource.

    A specific input such as ``project_id`` can match the project catalog
    directly. A generic path input named only ``id`` is accepted when the
    current operation's final static path segment names that resource. This
    keeps project IDs away from unrelated scalar inputs such as
    ``min_access_level`` while preserving common ``/projects/{id}`` schemas.

    Args:
        canonical_resource: Stable resource name from the monitor catalog.
        aliases: Other observed names for the same resource.
        input_names: Leaf and ancestor names for the consuming input.
        operation_path: Current OpenAPI path template used to interpret a
            generic ``id`` input.

    Returns:
        Whether the resource identifier pool is semantically safe to offer.
    """
    resource_names = {
        _singular_resource_name(value)
        for value in [canonical_resource, *aliases]
        if value
    }
    normalized_inputs = {
        _normalize_reference_name(value)
        for value in input_names
        if value
    }
    for resource_name in resource_names:
        if normalized_inputs.intersection(
            {
                resource_name,
                f"{resource_name}id",
                f"{resource_name}identifier",
            }
        ):
            return True

    if normalized_inputs.intersection({"id", "identifier"}):
        static_segments = [
            segment
            for segment in operation_path.strip("/").split("/")
            if segment
            and not (
                segment.startswith("{")
                and segment.endswith("}")
            )
        ]
        operation_resource = _singular_resource_name(
            static_segments[-1] if static_segments else ""
        )
        return operation_resource in resource_names
    return False


def _normalize_reference_name(value: str) -> str:
    """Return a lowercase alphanumeric name for semantic comparison.

    Args:
        value: An OpenAPI or monitor-provided name.

    Returns:
        The normalized name. No external or persistent state is changed.
    """
    return re.sub(r"[^a-z0-9]+", "", value.casefold())


def _singular_resource_name(value: str) -> str:
    """Normalize common singular/plural resource spellings.

    Args:
        value: A canonical resource, alias, or static path segment.

    Returns:
        A normalized singular spelling used only for conservative matching.
    """
    normalized = _normalize_reference_name(value)
    if normalized.endswith("ies") and len(normalized) > 3:
        return f"{normalized[:-3]}y"
    if normalized.endswith("s") and not normalized.endswith("ss"):
        return normalized[:-1]
    return normalized


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


def _resource_pool_compatible(
    expected_type: str | None,
    value_types: list[str],
) -> bool:
    """Check every stored value type against the consuming JSON input type."""
    if not value_types:
        return False
    if expected_type is None:
        return True
    return all(
        value_type == expected_type
        or (expected_type == "number" and value_type == "integer")
        for value_type in value_types
    )


def _option_id(input_node_id: str, kind: str, name: str) -> str:
    """Create an opaque, stable prompt alias without exposing catalog IDs."""
    digest = sha256(
        f"{input_node_id}\0{kind}\0{name}".encode("utf-8")
    ).hexdigest()[:20]
    return f"ref_{digest}"
