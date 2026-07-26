"""Deterministic finite-domain solving for same-request constraints."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from copy import deepcopy
import hashlib
from typing import Any

from .constraints import (
    ConstraintSet,
    InputAssignment,
    InputNodeOverride,
    evaluate_constraint_set,
    evaluate_constraint_set_partial,
    referenced_input_node_ids,
    validate_constraint_set,
)
from .models import (
    BooleanGenerator,
    ChoiceGenerator,
    ConstantGenerator,
    FormatGenerator,
    GeneratedTestCase,
    InputGeneratorConfig,
    InputNodeSnapshot,
    IntegerRangeGenerator,
    NumberRangeGenerator,
    OperationGeneratorConfig,
    OperationTestSnapshot,
    RandomStringGenerator,
    ResourceIdentifierGenerator,
    ResponseValueGenerator,
)
from .ports import ReferenceValueProvider


class ConstraintSolveError(ValueError):
    """A bounded constraint search cannot produce a safe request."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        input_node_ids: Sequence[str] = (),
    ) -> None:
        super().__init__(message)
        self.code = code
        self.input_node_ids = tuple(input_node_ids)


def build_candidate_domains(
    *,
    operation: OperationTestSnapshot,
    config: OperationGeneratorConfig,
    constraints: ConstraintSet,
    baseline: GeneratedTestCase,
    run_seed: int,
    case_index: int,
    reference_values: ReferenceValueProvider | None = None,
    max_domain_size: int = 8,
) -> dict[str, tuple[InputNodeOverride, ...]]:
    """Build bounded baseline-first override domains for referenced inputs."""

    if max_domain_size < 1:
        raise ValueError("max_domain_size must be positive")
    validate_constraint_set(constraints, operation)
    nodes = {node.input_node_id: node for node in operation.input_nodes}
    configs = {item.input_node_id: item for item in config.configs}
    baseline_assignments = assignments_from_generated_case(operation, baseline)
    result: dict[str, tuple[InputNodeOverride, ...]] = {}
    for input_node_id in referenced_input_node_ids(constraints):
        node = nodes[input_node_id]
        generator_config = configs.get(input_node_id)
        if generator_config is None:
            raise ConstraintSolveError(
                "constraint_empty_domain",
                f"Missing Generator configuration for {input_node_id}",
                input_node_ids=(input_node_id,),
            )
        baseline_assignment = baseline_assignments.get(
            input_node_id,
            InputAssignment(present=False),
        )
        domain = _candidate_domain(
            node=node,
            config=generator_config,
            baseline=baseline_assignment,
            run_seed=run_seed,
            case_index=case_index,
            reference_values=reference_values,
            max_domain_size=max_domain_size,
        )
        if not domain:
            raise ConstraintSolveError(
                "constraint_empty_domain",
                f"No candidate values are available for {input_node_id}",
                input_node_ids=(input_node_id,),
            )
        result[input_node_id] = domain
    return result


def solve_input_overrides(
    *,
    operation: OperationTestSnapshot,
    config: OperationGeneratorConfig,
    constraints: ConstraintSet,
    baseline: GeneratedTestCase,
    run_seed: int,
    case_index: int,
    reference_values: ReferenceValueProvider | None = None,
    max_domain_size: int = 8,
    max_search_states: int = 10_000,
) -> dict[str, InputNodeOverride]:
    """Return the first deterministic joint assignment satisfying all constraints."""

    if max_search_states < 1:
        raise ValueError("max_search_states must be positive")
    domains = build_candidate_domains(
        operation=operation,
        config=config,
        constraints=constraints,
        baseline=baseline,
        run_seed=run_seed,
        case_index=case_index,
        reference_values=reference_values,
        max_domain_size=max_domain_size,
    )
    reference_counts = _reference_counts(constraints)
    ordered_ids = sorted(
        domains,
        key=lambda node_id: (
            len(domains[node_id]),
            -reference_counts[node_id],
            node_id,
        ),
    )
    baseline_assignments = assignments_from_generated_case(operation, baseline)
    nodes = {node.input_node_id: node for node in operation.input_nodes}
    selected: dict[str, InputNodeOverride] = {}
    explored = 0
    exhausted = False

    def search(index: int) -> dict[str, InputNodeOverride] | None:
        nonlocal explored, exhausted
        if index == len(ordered_ids):
            if explored >= max_search_states:
                exhausted = True
                return None
            explored += 1
            completed = _complete_candidate(
                selected,
                baseline=baseline_assignments,
                nodes=nodes,
            )
            if completed is None:
                return None
            assignments, overrides = completed
            if evaluate_constraint_set(constraints, assignments):
                return overrides
            return None
        node_id = ordered_ids[index]
        for candidate in domains[node_id]:
            selected[node_id] = candidate
            if index + 1 < len(ordered_ids):
                partial = _partial_candidate(selected, nodes=nodes)
                if partial is None or evaluate_constraint_set_partial(
                    constraints,
                    partial,
                ) is False:
                    continue
            result = search(index + 1)
            if result is not None:
                return result
            if exhausted:
                return None
        selected.pop(node_id, None)
        return None

    solution = search(0)
    if solution is not None:
        return solution
    if exhausted:
        raise ConstraintSolveError(
            "constraint_search_exhausted",
            f"Constraint search exceeded {max_search_states} assignments",
            input_node_ids=ordered_ids,
        )
    raise ConstraintSolveError(
        "constraint_unsatisfiable",
        "No Generator candidate assignment satisfies all constraints",
        input_node_ids=ordered_ids,
    )


def assignments_from_generated_case(
    operation: OperationTestSnapshot,
    generated: GeneratedTestCase,
) -> dict[str, InputAssignment]:
    """Recover stable input assignments from generated request evidence."""

    nodes = {node.input_node_id: node for node in operation.input_nodes}
    assignments = {
        node_id: InputAssignment(present=False)
        for node_id in nodes
    }
    omitted = set(generated.omitted_input_node_ids)
    location_values = {
        "path": generated.path_parameters,
        "query": generated.query_parameters,
        "header": generated.header_parameters,
        "cookie": generated.cookie_parameters,
    }
    for parameter in operation.parameters:
        values = location_values[parameter.location]
        if parameter.name not in values:
            continue
        node = nodes[parameter.input_node_id]
        assignments[parameter.input_node_id] = _assignment_for_generated_value(
            node,
            values[parameter.name],
        )
    for item in generated.generated_values:
        if item.input_node_id not in nodes or item.input_node_id in omitted:
            continue
        assignments[item.input_node_id] = InputAssignment(
            present=True,
            has_value=True,
            value=deepcopy(item.value),
        )
        _mark_ancestors_present(
            item.input_node_id,
            assignments=assignments,
            nodes=nodes,
        )
    if generated.body_present and operation.request_body_node_id is not None:
        assignments[operation.request_body_node_id] = InputAssignment(present=True)
        media_node_id = operation.media_type_node_ids.get(
            (generated.media_type or "").strip().lower()
        )
        if media_node_id is not None:
            assignments[media_node_id] = _assignment_for_generated_value(
                nodes[media_node_id],
                generated.body,
            )
            _mark_ancestors_present(
                media_node_id,
                assignments=assignments,
                nodes=nodes,
            )
            _mark_body_descendants(
                media_node_id,
                generated.body,
                assignments=assignments,
                nodes=nodes,
            )
    for input_node_id in omitted:
        if input_node_id in assignments:
            assignments[input_node_id] = InputAssignment(present=False)
    return assignments


def _mark_body_descendants(
    parent_id: str,
    value: Any,
    *,
    assignments: dict[str, InputAssignment],
    nodes: Mapping[str, InputNodeSnapshot],
) -> None:
    parent = nodes[parent_id]
    prefix = f"{parent.canonical_path}/properties/"
    for child in nodes.values():
        if (
            child.parent_node_id != parent_id
            or not child.canonical_path.startswith(prefix)
        ):
            continue
        encoded_name = child.canonical_path.removeprefix(prefix).split("/", 1)[0]
        name = encoded_name.replace("~1", "/").replace("~0", "~")
        if not isinstance(value, dict) or name not in value:
            continue
        child_value = value[name]
        assignments[child.input_node_id] = _assignment_for_generated_value(
            child,
            child_value,
        )
        _mark_body_descendants(
            child.input_node_id,
            child_value,
            assignments=assignments,
            nodes=nodes,
        )


def _assignment_for_generated_value(
    node: InputNodeSnapshot,
    value: Any,
) -> InputAssignment:
    schema = node.schema_contract
    declared = (
        {schema.type}
        if schema is not None and isinstance(schema.type, str)
        else set(schema.type or ())
        if schema is not None
        else set()
    )
    is_container = (
        schema is None
        or bool(schema.properties)
        or schema.items is not None
        or bool(schema.all_of or schema.any_of or schema.one_of)
        or bool(declared.intersection({"object", "array"}))
    )
    if is_container:
        return InputAssignment(present=True)
    return InputAssignment(
        present=True,
        has_value=True,
        value=deepcopy(value),
    )


def _candidate_domain(
    *,
    node: InputNodeSnapshot,
    config: InputGeneratorConfig,
    baseline: InputAssignment,
    run_seed: int,
    case_index: int,
    reference_values: ReferenceValueProvider | None,
    max_domain_size: int,
) -> tuple[InputNodeOverride, ...]:
    presence = _presence_candidates(node, config, baseline)
    values = _value_candidates(
        config,
        baseline=baseline,
        run_seed=run_seed,
        case_index=case_index,
        reference_values=reference_values,
        max_domain_size=max_domain_size,
    )
    scalar = _config_generates_scalar(config)
    result: list[InputNodeOverride] = []
    baseline_override = InputNodeOverride.model_validate(baseline.model_dump())
    for present in presence:
        if not present:
            _append_unique(result, InputNodeOverride(present=False))
            continue
        if scalar:
            for value in values:
                _append_unique(
                    result,
                    InputNodeOverride(
                        present=True,
                        has_value=True,
                        value=deepcopy(value),
                    ),
                )
        else:
            _append_unique(result, InputNodeOverride(present=True))
    if baseline_override in result:
        result.remove(baseline_override)
        result.insert(0, baseline_override)
    return tuple(result)


def _presence_candidates(
    node: InputNodeSnapshot,
    config: InputGeneratorConfig,
    baseline: InputAssignment,
) -> tuple[bool, ...]:
    if node.required or config.inclusion_probability >= 1:
        return (True,)
    if config.inclusion_probability <= 0:
        return (False,)
    return (baseline.present, not baseline.present)


def _value_candidates(
    config: InputGeneratorConfig,
    *,
    baseline: InputAssignment,
    run_seed: int,
    case_index: int,
    reference_values: ReferenceValueProvider | None,
    max_domain_size: int,
) -> tuple[Any, ...]:
    strategy = config.strategy
    result: list[Any] = []
    if baseline.present and baseline.has_value:
        _append_typed_unique(result, deepcopy(baseline.value))
    if isinstance(strategy, ConstantGenerator):
        _append_typed_unique(result, deepcopy(strategy.value))
    elif isinstance(strategy, ChoiceGenerator):
        for value in strategy.values:
            _append_typed_unique(result, deepcopy(value))
    elif isinstance(strategy, BooleanGenerator):
        _append_typed_unique(result, False)
        _append_typed_unique(result, True)
    elif isinstance(strategy, IntegerRangeGenerator):
        for value in (
            strategy.minimum,
            (strategy.minimum + strategy.maximum) // 2,
            strategy.maximum,
        ):
            _append_typed_unique(result, value)
        _append_generated_samples(
            result,
            config=config,
            run_seed=run_seed,
            case_index=case_index,
            reference_values=reference_values,
            max_domain_size=max_domain_size,
        )
    elif isinstance(strategy, NumberRangeGenerator):
        for value in (
            strategy.minimum,
            (strategy.minimum + strategy.maximum) / 2,
            strategy.maximum,
        ):
            _append_typed_unique(result, value)
        _append_generated_samples(
            result,
            config=config,
            run_seed=run_seed,
            case_index=case_index,
            reference_values=reference_values,
            max_domain_size=max_domain_size,
        )
    elif isinstance(strategy, RandomStringGenerator | FormatGenerator):
        _append_generated_samples(
            result,
            config=config,
            run_seed=run_seed,
            case_index=case_index,
            reference_values=reference_values,
            max_domain_size=max_domain_size,
        )
    elif isinstance(
        strategy,
        ResourceIdentifierGenerator | ResponseValueGenerator,
    ):
        values = (
            reference_values.values_for(strategy)
            if reference_values is not None
            else ()
        )
        for value in values:
            _append_typed_unique(result, deepcopy(value))
    return tuple(result[:max_domain_size])


def _append_generated_samples(
    result: list[Any],
    *,
    config: InputGeneratorConfig,
    run_seed: int,
    case_index: int,
    reference_values: ReferenceValueProvider | None,
    max_domain_size: int,
) -> None:
    from .generation import generate_strategy_value

    for sample_index in range(max_domain_size * 2):
        if len(result) >= max_domain_size:
            return
        seed = _seed(
            run_seed,
            case_index,
            config.input_node_id,
            sample_index,
        )
        try:
            value = generate_strategy_value(
                config.strategy,
                seed=seed,
                reference_values=reference_values,
            )
        except ValueError:
            return
        _append_typed_unique(result, value)


def _complete_candidate(
    selected: Mapping[str, InputNodeOverride],
    *,
    baseline: Mapping[str, InputAssignment],
    nodes: Mapping[str, InputNodeSnapshot],
) -> tuple[
    dict[str, InputAssignment],
    dict[str, InputNodeOverride],
] | None:
    assignments = dict(baseline)
    overrides = dict(selected)
    for node_id, override in selected.items():
        assignments[node_id] = InputAssignment.model_validate(
            override.model_dump()
        )
    for node_id, override in tuple(overrides.items()):
        if not override.present:
            continue
        parent_id = nodes[node_id].parent_node_id
        while parent_id is not None:
            explicit = selected.get(parent_id)
            if explicit is not None and not explicit.present:
                return None
            assignments[parent_id] = InputAssignment(present=True)
            if not baseline.get(parent_id, InputAssignment(present=False)).present:
                overrides[parent_id] = InputNodeOverride(present=True)
            parent_id = nodes[parent_id].parent_node_id
    return assignments, overrides


def _partial_candidate(
    selected: Mapping[str, InputNodeOverride],
    *,
    nodes: Mapping[str, InputNodeSnapshot],
) -> dict[str, InputAssignment] | None:
    assignments = {
        node_id: InputAssignment.model_validate(override.model_dump())
        for node_id, override in selected.items()
    }
    for node_id, override in selected.items():
        if not override.present:
            continue
        parent_id = nodes[node_id].parent_node_id
        while parent_id is not None:
            explicit = selected.get(parent_id)
            if explicit is not None and not explicit.present:
                return None
            assignments[parent_id] = InputAssignment(present=True)
            parent_id = nodes[parent_id].parent_node_id
    return assignments


def _mark_ancestors_present(
    node_id: str,
    *,
    assignments: dict[str, InputAssignment],
    nodes: Mapping[str, InputNodeSnapshot],
) -> None:
    parent_id = nodes[node_id].parent_node_id
    while parent_id is not None:
        assignments[parent_id] = InputAssignment(present=True)
        parent_id = nodes[parent_id].parent_node_id


def _config_generates_scalar(config: InputGeneratorConfig) -> bool:
    return isinstance(
        config.strategy,
        ConstantGenerator
        | ChoiceGenerator
        | BooleanGenerator
        | IntegerRangeGenerator
        | NumberRangeGenerator
        | RandomStringGenerator
        | FormatGenerator
        | ResourceIdentifierGenerator
        | ResponseValueGenerator,
    )


def _reference_counts(constraints: ConstraintSet) -> Counter[str]:
    result: Counter[str] = Counter()

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            node_id = value.get("input_node_id")
            if isinstance(node_id, str):
                result[node_id] += 1
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(constraints.model_dump(mode="python"))
    return result


def _append_unique(
    result: list[InputNodeOverride],
    value: InputNodeOverride,
) -> None:
    if value not in result:
        result.append(value)


def _append_typed_unique(result: list[Any], value: Any) -> None:
    key = _typed_key(value)
    if all(_typed_key(existing) != key for existing in result):
        result.append(value)


def _typed_key(value: Any) -> Any:
    if isinstance(value, dict):
        return (
            "dict",
            tuple(
                sorted(
                    (str(key), _typed_key(item))
                    for key, item in value.items()
                )
            ),
        )
    if isinstance(value, list):
        return ("list", tuple(_typed_key(item) for item in value))
    return (type(value).__name__, repr(value))


def _seed(
    run_seed: int,
    case_index: int,
    input_node_id: str,
    sample_index: int,
) -> int:
    payload = (
        f"{run_seed}\0{case_index}\0{input_node_id}\0"
        f"constraint-domain\0{sample_index}"
    )
    return int.from_bytes(hashlib.sha256(payload.encode()).digest()[:8], "big")


__all__ = [
    "ConstraintSolveError",
    "assignments_from_generated_case",
    "build_candidate_domains",
    "solve_input_overrides",
]
