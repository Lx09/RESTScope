"""Compile semantic Patch scope, Constraints, variants, and scalar compatibility.

The runtime performs evidence lookups and orchestration.  This private module
contains deterministic transformations that need no database or Tool backend,
keeping input-node identities and recursive Constraint mechanics behind the
single Patch runtime Interface.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence

from ..constraints import (
    ConstraintSet,
    OperationConstraintRecord,
    classify_constraint,
    normalize_constraint_set,
    referenced_input_node_ids,
    validate_constraint_set,
)
from ..models import InputGeneratorPatch, OperationGeneratorConfig
from ..semantics import build_semantic_input_map
from .errors import ParameterPatchValidationError
from .models import CompiledConstraintPatch, SemanticBooleanExpression


def validate_affected_inputs(
    config: OperationGeneratorConfig,
    affected_inputs: Sequence[str],
) -> tuple[str, ...]:
    """Require 1–20 unique semantic handles present in the operation."""
    values = tuple(affected_inputs)
    if not 1 <= len(values) <= 20 or len(values) != len(set(values)):
        raise ParameterPatchValidationError(
            "invalid_affected_inputs",
            "affected_inputs must contain 1 to 20 unique semantic handles",
        )
    semantic = build_semantic_input_map(config)
    unknown = sorted(set(values) - set(semantic.node_by_handle))
    if unknown:
        raise ParameterPatchValidationError(
            "unknown_affected_inputs",
            "Unknown semantic inputs: " + ", ".join(unknown),
        )
    return values


def compile_constraint(
    expression: SemanticBooleanExpression,
    *,
    allowed_handles: set[str],
    semantic: Mapping[str, str],
    config: OperationGeneratorConfig,
) -> CompiledConstraintPatch:
    """Compile one recursive semantic expression into normalized node IDs."""
    source = expression.model_dump(mode="python")

    def convert(value: object) -> object:
        if isinstance(value, list):
            return [convert(item) for item in value]
        if not isinstance(value, dict):
            return value
        output = {key: convert(item) for key, item in value.items()}
        if value.get("type") in {"present", "input_value"}:
            handle = value.get("input")
            if not isinstance(handle, str) or handle not in semantic:
                raise ParameterPatchValidationError(
                    "unknown_constraint_input",
                    f"Unknown Constraint input: {handle}",
                )
            if handle not in allowed_handles:
                raise ParameterPatchValidationError(
                    "constraint_input_out_of_scope",
                    f"Constraint input is outside affected_inputs: {handle}",
                )
            output.pop("input", None)
            output["input_node_id"] = semantic[handle]
        return output

    compiled = ConstraintSet.model_validate({"constraints": [convert(source)]})
    validate_constraint_set(compiled, config.snapshot)
    normalized = normalize_constraint_set(compiled)
    encoded = json.dumps(
        normalized.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
    )
    identity = hashlib.sha256(
        f"{config.operation_key}:{encoded}".encode()
    ).hexdigest()[:16]
    return CompiledConstraintPatch(
        constraint_id=f"constraint_{identity}",
        kind=classify_constraint(normalized.constraints[0]),
        constraint=normalized,
    )


def replace_constraint_scope(
    *,
    current: Sequence[OperationConstraintRecord],
    replacement: Sequence[CompiledConstraintPatch],
    affected_node_ids: set[str],
    operation_key: str,
) -> tuple[OperationConstraintRecord, ...]:
    """Replace the complete direct and transitive affected Constraint closure."""
    frontier = set(affected_node_ids)
    removed_ids: set[str] = set()
    changed = True
    while changed:
        changed = False
        for item in current:
            if item.id in removed_ids:
                continue
            owners = set(item.owner_input_node_ids)
            if owners & frontier:
                removed_ids.add(item.id)
                frontier.update(owners)
                changed = True
    retained = [item for item in current if item.id not in removed_ids]
    added = [
        OperationConstraintRecord(
            id=item.constraint_id,
            operation_key=operation_key,
            owner_input_node_ids=tuple(
                sorted(referenced_input_node_ids(item.constraint))
            ),
            kind=item.kind,
            constraint=item.constraint,
        )
        for item in replacement
    ]
    return tuple(sorted((*retained, *added), key=lambda item: item.id))


def reference_expected_type(
    config: OperationGeneratorConfig,
    input_node_id: str,
) -> str | None:
    """Return a body scalar type or ``None`` for stringified parameters."""
    if input_node_id in {item.input_node_id for item in config.snapshot.parameters}:
        return None
    node = next(
        item for item in config.snapshot.input_nodes if item.input_node_id == input_node_id
    )
    declared = node.schema_contract.type if node.schema_contract is not None else None
    if isinstance(declared, list):
        concrete = [item for item in declared if item != "null"]
        declared = concrete[0] if len(concrete) == 1 else None
    if declared not in {"string", "integer", "number", "boolean"}:
        raise ParameterPatchValidationError(
            "reference_input_not_scalar",
            "Reference values can only target scalar inputs",
        )
    return declared


def json_scalar_type(value: object) -> str:
    """Classify one JSON scalar without treating Boolean as integer."""
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    return "non_scalar"


def reference_types_compatible(expected: str | None, values: Sequence[str]) -> bool:
    """Require every possible referenced value to fit the consumer input."""
    if not values or "non_scalar" in values:
        return False
    if expected is None:
        return True
    return all(
        value == expected or (expected == "number" and value == "integer")
        for value in values
    )


def validate_variant_branch_updates(
    *,
    config: OperationGeneratorConfig,
    updates: Sequence[InputGeneratorPatch],
    handle_by_node: Mapping[str, str],
) -> None:
    """Require exclusive parent Variant selection for every changed branch."""
    nodes = {item.input_node_id: item for item in config.snapshot.input_nodes}
    current_configs = {item.input_node_id: item for item in config.positive_generators}
    updates_by_node = {item.input_node_id: item for item in updates}
    for changed_node_id in updates_by_node:
        branch_node_id = changed_node_id
        current = nodes[changed_node_id]
        while current.parent_node_id is not None:
            parent = nodes[current.parent_node_id]
            parent_config = current_configs[parent.input_node_id]
            if parent_config.strategy.type == "variant":
                parent_update = updates_by_node.get(parent.input_node_id)
                parent_strategy = parent_update.strategy if parent_update else None
                parent_handle = handle_by_node.get(
                    parent.input_node_id,
                    parent.canonical_path,
                )
                if parent_strategy is None or parent_strategy.type != "variant":
                    raise ParameterPatchValidationError(
                        "variant_selection_missing",
                        f"{parent_handle} must select the changed variant branch",
                    )
                branch_children = sorted(
                    (
                        node
                        for node in nodes.values()
                        if node.parent_node_id == parent.input_node_id
                        and (
                            "/oneOf/" in node.canonical_path
                            or "/anyOf/" in node.canonical_path
                        )
                    ),
                    key=_variant_branch_index,
                )
                selected_index = next(
                    (
                        index
                        for index, node in enumerate(branch_children)
                        if node.input_node_id == branch_node_id
                    ),
                    None,
                )
                weights = parent_strategy.branch_weights
                if (
                    selected_index is None
                    or selected_index >= len(weights)
                    or weights[selected_index] <= 0
                    or any(
                        weight > 0
                        for index, weight in enumerate(weights)
                        if index != selected_index
                    )
                ):
                    raise ParameterPatchValidationError(
                        "variant_selection_ambiguous",
                        f"{parent_handle} must exclusively select the changed branch",
                    )
            branch_node_id = parent.input_node_id
            current = parent


def _variant_branch_index(node: object) -> int:
    """Return one branch's numeric OpenAPI position for stable ordering."""
    try:
        return int(node.canonical_path.rstrip("/").rsplit("/", 1)[-1])
    except ValueError:
        return 0
