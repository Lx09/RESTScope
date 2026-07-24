"""Adapters from API behavior evidence to reference-backed generators."""

from __future__ import annotations

from hashlib import sha256

from restscope.agent.api_behavior_monitor import (
    APIBehaviorMonitorAgent,
    ResourceLookupRequest,
    ResponseValueSource,
)
from restscope.testing import (
    InputGeneratorPatch,
    OperationGeneratorConfig,
    ResourceIdentifierGenerator,
    ResponseValueGenerator,
)

from .schemas import AvailableReferenceOption


_MAX_REFERENCE_OPTIONS = 100


class BehaviorMonitorReferenceValues:
    """Resolve both resource identifiers and generic monitored response values."""

    def __init__(self, agent: APIBehaviorMonitorAgent) -> None:
        self.agent = agent

    def values_for(
        self,
        strategy: ResourceIdentifierGenerator | ResponseValueGenerator,
    ) -> list[object]:
        if isinstance(strategy, ResponseValueGenerator):
            return self.agent.response_values_for(strategy.value_name)
        result = self.agent.lookup(
            ResourceLookupRequest(
                resource=strategy.resource,
                limit=100,
            )
        )
        return [item.value for item in result.identifiers]

    def available_options(
        self,
        *,
        ir,
        config: OperationGeneratorConfig,
        input_node_ids: set[str] | None = None,
    ) -> list[AvailableReferenceOption]:
        """Describe only reference generators whose persistent pool is non-empty."""

        nodes = {
            item.input_node_id: item for item in config.snapshot.input_nodes
        }
        options: list[AvailableReferenceOption] = []

        # A response option is consumer-specific, so expose it before the
        # cross-product of resource pools and inputs reaches the hard bound.
        available_response_value_sources = getattr(
            self.agent,
            "available_response_value_sources",
            None,
        )
        if callable(available_response_value_sources):
            for item in config.configs:
                if (
                    input_node_ids is not None
                    and item.input_node_id not in input_node_ids
                ):
                    continue
                node = nodes.get(item.input_node_id)
                if node is None:
                    continue
                expected_type = (
                    _expected_type(node.schema_contract.type)
                    if node.schema_contract is not None
                    else None
                )
                source_options = available_response_value_sources(
                    ir=ir,
                    consumer_operation_key=config.operation_key,
                    consumer_input_node_id=item.input_node_id,
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

        catalog = getattr(self.agent, "catalog", None)
        if catalog is None:
            return options
        resources = catalog.list_resources(
            limit=_MAX_REFERENCE_OPTIONS,
            aliases_per_resource=0,
        )
        populated_resources = []
        for resource in resources:
            lookup = self.agent.lookup(
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
            expected_type = (
                _expected_type(node.schema_contract.type)
                if node is not None and node.schema_contract is not None
                else None
            )
            for resource, lookup in populated_resources:
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
        ir,
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
            parameter_name = (
                parameter.name
                if parameter is not None
                else node.canonical_path.rsplit(".", 1)[-1]
            )
            expected_type = (
                _expected_type(node.schema_contract.type)
                if node.schema_contract is not None
                else None
            )
            selected = selected_by_input.get(update.input_node_id)
            register_selected = getattr(
                self.agent,
                "register_response_value_sources",
                None,
            )
            if (
                selected is not None
                and selected.kind == "response_value"
                and callable(register_selected)
            ):
                if selected.value_name != strategy.value_name:
                    raise ValueError(
                        "Selected response option does not match Generator"
                    )
                assert selected.producer_status_code is not None
                assert selected.producer_media_type is not None
                assert selected.source_selector is not None
                assert selected.source_field is not None
                registration = register_selected(
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
                registration = self.agent.register_response_value(
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


def _expected_type(value: str | list[str] | None) -> str | None:
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
    digest = sha256(
        f"{input_node_id}\0{kind}\0{name}".encode("utf-8")
    ).hexdigest()[:20]
    return f"ref_{digest}"
