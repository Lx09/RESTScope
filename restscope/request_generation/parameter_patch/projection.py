"""Project internal Parameter Patch state into bounded semantic Tool results.

The Patch runtime owns internal input-node identities and compiled Constraint
records.  This module is the single output boundary that replaces those IDs
with operation-local semantic handles and fails closed when a complete result
would exceed the Tool limit.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Mapping, Sequence

from ..constraints import OperationConstraintRecord
from ..models import OperationGeneratorConfig
from ..semantics import build_semantic_input_map
from ..store import ReferenceValueBinding, RequestGenerationState
from .errors import ParameterPatchValidationError

if TYPE_CHECKING:
    from .runtime import ValidatedPatch


MAX_TOOL_OUTPUT_CHARACTERS = 24_000


def constraint_closure(
    state: RequestGenerationState,
    input_handles: Sequence[str],
) -> tuple[tuple[OperationConstraintRecord, ...], tuple[str, ...]]:
    """Return all active Constraints transitively connected to selected inputs."""
    selected = _validate_affected_inputs(state.config, input_handles)
    semantic = build_semantic_input_map(state.config)
    frontier = {semantic.node_by_handle[item] for item in selected}
    included: dict[str, OperationConstraintRecord] = {}
    changed = True
    while changed:
        changed = False
        for item in state.constraints:
            if item.id in included:
                continue
            owners = set(item.owner_input_node_ids)
            if owners & frontier:
                included[item.id] = item
                frontier.update(owners)
                changed = True
    extra = sorted(
        semantic.handle_by_node[node_id]
        for node_id in frontier
        if semantic.handle_by_node[node_id] not in selected
    )
    return tuple(included[key] for key in sorted(included)), tuple(extra)


def semantic_state_payload(
    state: RequestGenerationState,
    input_handles: Sequence[str],
) -> dict[str, Any]:
    """Project selected Generators and their complete Constraint closure."""
    selected = _validate_affected_inputs(state.config, input_handles)
    semantic = build_semantic_input_map(state.config)
    configs = {item.input_node_id: item for item in state.config.configs}
    closure, extra = constraint_closure(state, selected)
    handles = tuple(dict.fromkeys((*selected, *extra)))
    payload = {
        "operation_key": state.config.operation_key,
        "revision": state.revision,
        "state_digest": state.state_digest,
        "last_applied_validation_digest": state.last_applied_validation_digest,
        "inputs": [
            {
                "input": handle,
                "generator": {
                    "inclusion_probability": configs[
                        semantic.node_by_handle[handle]
                    ].inclusion_probability,
                    "strategy": _project_generator_strategy(
                        configs[semantic.node_by_handle[handle]].strategy,
                        binding=_binding_for(
                            state.reference_bindings,
                            semantic.node_by_handle[handle],
                        ),
                    ),
                },
            }
            for handle in handles
        ],
        "constraints": [
            _semantic_constraint_record(item, semantic.handle_by_node)
            for item in closure
        ],
        "additional_constraint_inputs": list(extra),
    }
    _require_bounded_payload(payload)
    return payload


def validation_payload(validated: "ValidatedPatch") -> dict[str, Any]:
    """Project complete post-Patch state and bounded deterministic witnesses."""
    semantic = build_semantic_input_map(validated.final_config)
    configs = {item.input_node_id: item for item in validated.final_config.configs}
    final_handles = tuple(
        dict.fromkeys(
            (
                *validated.affected_inputs,
                *(
                    semantic.handle_by_node[item.input_node_id]
                    for item in validated.compiled_patch.updates
                ),
            )
        )
    )
    payload = {
        "operation_key": validated.operation_key,
        "revision": validated.expected_revision,
        "state_digest": validated.state_digest,
        "validation_digest": validated.validation_digest,
        "affected_inputs": list(validated.affected_inputs),
        "final_generators": [
            {
                "input": handle,
                "inclusion_probability": configs[
                    semantic.node_by_handle[handle]
                ].inclusion_probability,
                "strategy": _project_generator_strategy(
                    configs[semantic.node_by_handle[handle]].strategy,
                    binding=_binding_for(
                        validated.final_reference_bindings,
                        semantic.node_by_handle[handle],
                    ),
                ),
            }
            for handle in final_handles
        ],
        "domain_analysis": list(validated.domain_analysis),
        "final_constraints": [
            _semantic_constraint_record(item, semantic.handle_by_node)
            for item in validated.final_constraints
        ],
        "constraint_participants": sorted(
            {
                semantic.handle_by_node[node_id]
                for item in validated.final_constraints
                for node_id in item.owner_input_node_ids
            }
        ),
        "samples": list(validated.samples),
    }
    _require_bounded_payload(payload)
    return payload


def _validate_affected_inputs(
    config: OperationGeneratorConfig,
    affected_inputs: Sequence[str],
) -> tuple[str, ...]:
    """Require 1–20 unique semantic handles present in the operation."""
    values = tuple(affected_inputs)
    if not 1 <= len(values) <= 20 or len(values) != len(set(values)):
        raise ParameterPatchValidationError(
            "invalid_affected_inputs",
            "affected_inputs must contain 1 to 20 unique handles",
        )
    semantic = build_semantic_input_map(config)
    unknown = sorted(set(values) - set(semantic.node_by_handle))
    if unknown:
        raise ParameterPatchValidationError(
            "unknown_affected_inputs",
            "Unknown semantic inputs: " + ", ".join(unknown),
        )
    return values


def _binding_for(
    bindings: Sequence[ReferenceValueBinding],
    input_node_id: str,
) -> ReferenceValueBinding | None:
    """Find the immutable reference identity for one projected input."""
    return next(
        (item for item in bindings if item.input_node_id == input_node_id),
        None,
    )


def _project_generator_strategy(
    strategy: Any,
    *,
    binding: ReferenceValueBinding | None,
) -> dict[str, Any]:
    """Add exact response producer identity to an otherwise semantic Generator."""
    payload = strategy.model_dump(mode="json")
    if binding is not None and binding.kind == "response_value":
        payload = {
            "type": "response_value",
            "source": {
                "operation_key": binding.producer_operation_key,
                "matched_status_code": binding.producer_status_code,
                "media_type": binding.producer_media_type,
                "field": binding.source_field,
            },
        }
    return payload


def _semantic_constraint_record(
    item: OperationConstraintRecord,
    handle_by_node: Mapping[str, str],
) -> dict[str, Any]:
    """Replace internal node IDs with semantic handles in one Tool result."""
    def convert(value: Any) -> Any:
        if isinstance(value, list):
            return [convert(child) for child in value]
        if not isinstance(value, dict):
            return value
        output = {key: convert(child) for key, child in value.items()}
        node_id = output.pop("input_node_id", None)
        if node_id is not None:
            output["input"] = handle_by_node[node_id]
        return output

    return {
        "id": item.id,
        "kind": item.kind,
        "expression": convert(item.constraint.model_dump(mode="json"))[
            "constraints"
        ],
    }


def _require_bounded_payload(payload: dict[str, Any]) -> None:
    """Fail closed rather than truncate Generator or Constraint state."""
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    if len(encoded) > MAX_TOOL_OUTPUT_CHARACTERS:
        raise ParameterPatchValidationError(
            "request_generation_output_too_large",
            "Complete Generator and Constraint state exceeds 24000 characters"
        )
