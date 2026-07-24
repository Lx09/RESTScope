"""Adapters from API behavior evidence to reference-backed generators."""

from __future__ import annotations

from restscope.agent.api_behavior_monitor import (
    APIBehaviorMonitorAgent,
    ResourceLookupRequest,
)
from restscope.testing import (
    InputGeneratorPatch,
    OperationGeneratorConfig,
    ResourceIdentifierGenerator,
    ResponseValueGenerator,
)


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

    def prepare_updates(
        self,
        *,
        ir,
        config: OperationGeneratorConfig,
        updates: list[InputGeneratorPatch],
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
            registration = self.agent.register_response_value(
                ir=ir,
                consumer_operation_key=config.operation_key,
                consumer_input_node_id=update.input_node_id,
                parameter_name=parameter_name,
                expected_type=_expected_type(node.schema_contract.type)
                if node.schema_contract is not None
                else None,
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
