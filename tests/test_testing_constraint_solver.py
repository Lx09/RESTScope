"""Regression scenarios for testing constraint solver. Each test documents one observable contract or failure boundary."""

from __future__ import annotations

import re

import pytest


class _ReferenceValues:
    def __init__(self, values: list[object]) -> None:
        self.values = values

    def values_for(self, strategy) -> list[object]:
        return list(self.values)


def _solver_config():
    from restscope.request_generation.models import (
        InputGeneratorConfig,
        InputNodeSnapshot,
        OperationGeneratorConfig,
        OperationTestSnapshot,
        ParameterSnapshot,
        SchemaSnapshot,
    )

    nodes = [
        InputNodeSnapshot(
            input_node_id="query/fixed",
            node_kind="parameter",
            canonical_path="query/fixed",
            required=True,
            schema_contract=SchemaSnapshot(type="integer"),
        ),
        InputNodeSnapshot(
            input_node_id="query/enabled",
            node_kind="parameter",
            canonical_path="query/enabled",
            required=False,
            schema_contract=SchemaSnapshot(type="boolean"),
        ),
        InputNodeSnapshot(
            input_node_id="query/mode",
            node_kind="parameter",
            canonical_path="query/mode",
            required=False,
            schema_contract=SchemaSnapshot(type="string"),
        ),
        InputNodeSnapshot(
            input_node_id="query/limit",
            node_kind="parameter",
            canonical_path="query/limit",
            required=True,
            schema_contract=SchemaSnapshot(type="integer"),
        ),
        InputNodeSnapshot(
            input_node_id="query/offset",
            node_kind="parameter",
            canonical_path="query/offset",
            required=True,
            schema_contract=SchemaSnapshot(type="integer"),
        ),
        InputNodeSnapshot(
            input_node_id="query/ratio",
            node_kind="parameter",
            canonical_path="query/ratio",
            required=True,
            schema_contract=SchemaSnapshot(type="number"),
        ),
        InputNodeSnapshot(
            input_node_id="query/ref",
            node_kind="parameter",
            canonical_path="query/ref",
            required=True,
            schema_contract=SchemaSnapshot(type="string"),
        ),
        InputNodeSnapshot(
            input_node_id="body",
            node_kind="request_body",
            canonical_path="body",
            required=False,
        ),
        InputNodeSnapshot(
            input_node_id="body/json",
            node_kind="media_type",
            canonical_path="body/application~1json",
            parent_node_id="body",
            required=True,
            schema_contract=SchemaSnapshot(
                type="object",
                properties={"count": SchemaSnapshot(type="integer")},
            ),
        ),
        InputNodeSnapshot(
            input_node_id="body/count",
            node_kind="property",
            canonical_path="body/application~1json/properties/count",
            parent_node_id="body/json",
            required=False,
            schema_contract=SchemaSnapshot(type="integer"),
        ),
    ]
    parameters = [
        ParameterSnapshot(
            input_node_id=node.input_node_id,
            name=node.input_node_id.removeprefix("query/"),
            location="query",
            required=node.required,
        )
        for node in nodes
        if node.node_kind == "parameter"
    ]
    snapshot = OperationTestSnapshot(
        operation_key="POST /solve",
        method="POST",
        path="/solve",
        parameters=parameters,
        request_body_node_id="body",
        media_type_node_ids={"application/json": "body/json"},
        available_media_types=["application/json"],
        input_nodes=nodes,
    )
    strategies = {
        "query/fixed": {"type": "constant", "value": 1},
        "query/enabled": {"type": "boolean", "true_probability": 0.5},
        "query/mode": {"type": "choice", "values": ["fast", "slow", "fast"]},
        "query/limit": {"type": "integer_range", "minimum": 1, "maximum": 10},
        "query/offset": {"type": "integer_range", "minimum": 1, "maximum": 10},
        "query/ratio": {"type": "number_range", "minimum": 0.5, "maximum": 2.5},
        "query/ref": {
            "type": "response_value",
            "source": {
                "producer_operation_id": "GET /known",
                "status_code": 200,
                "media_type": "application/json",
                "selector": "$.items[].id",
                "field_name": "id",
            },
        },
        "body": {"type": "request_body"},
        "body/json": {"type": "object"},
        "body/count": {"type": "integer_range", "minimum": 1, "maximum": 3},
    }
    configs = [
        InputGeneratorConfig(
            input_node_id=node.input_node_id,
            inclusion_probability=(
                1
                if node.required
                else 0.5
            ),
            strategy=strategies[node.input_node_id],
        )
        for node in nodes
    ]
    return OperationGeneratorConfig(
        operation_key=snapshot.operation_key,
        snapshot=snapshot,
        active_media_type="application/json",
        configs=configs,
    )


def _baseline():
    from restscope.request_generation.models import GeneratedNodeValue, GeneratedTestCase

    values = {
        "query/fixed": 1,
        "query/mode": "fast",
        "query/limit": 2,
        "query/offset": 8,
        "query/ratio": 1.5,
        "query/ref": "ref-a",
    }
    return GeneratedTestCase(
        operation_key="POST /solve",
        case_index=0,
        media_type=None,
        path_parameters={},
        query_parameters={
            "fixed": 1,
            "mode": "fast",
            "limit": 2,
            "offset": 8,
            "ratio": 1.5,
            "ref": "ref-a",
        },
        header_parameters={},
        cookie_parameters={},
        body=None,
        body_present=False,
        generated_values=[
            GeneratedNodeValue(
                input_node_id=node_id,
                instance_path=node_id.replace("/", "."),
                value=value,
            )
            for node_id, value in values.items()
        ],
        omitted_input_node_ids=["query/enabled", "body"],
    )


def _constraint_set(*expressions: dict):
    from restscope.request_generation.constraints import ConstraintSet

    return ConstraintSet.model_validate({"constraints": list(expressions)})


def test_candidate_domains_follow_generators_and_put_baseline_first() -> None:
    """Scenario: verify that candidate domains follow generators and put baseline first."""
    from restscope.request_generation.constraint_solver import build_candidate_domains

    config = _solver_config()
    constraints = _constraint_set(
        {"type": "present", "input_node_id": "query/fixed"},
        {"type": "present", "input_node_id": "query/enabled"},
        {
            "type": "compare",
            "operator": "==",
            "left": {"type": "input_value", "input_node_id": "query/mode"},
            "right": {"type": "literal", "value": "slow"},
        },
        {
            "type": "compare",
            "operator": ">",
            "left": {"type": "input_value", "input_node_id": "query/limit"},
            "right": {"type": "literal", "value": 5},
        },
        {
            "type": "compare",
            "operator": ">",
            "left": {"type": "input_value", "input_node_id": "query/ratio"},
            "right": {"type": "literal", "value": 1},
        },
        {
            "type": "compare",
            "operator": "==",
            "left": {"type": "input_value", "input_node_id": "query/ref"},
            "right": {"type": "literal", "value": "ref-b"},
        },
    )

    domains = build_candidate_domains(
        operation=config.snapshot,
        config=config,
        constraints=constraints,
        baseline=_baseline(),
        run_seed=31,
        case_index=0,
        reference_values=_ReferenceValues(["ref-b", "ref-a", "ref-b"]),
    )

    assert domains["query/fixed"] == (
        domains["query/fixed"][0].model_copy(),
    )
    assert domains["query/fixed"][0].value == 1
    assert domains["query/enabled"][0].present is False
    assert [
        item.value
        for item in domains["query/enabled"]
        if item.present
    ] == [False, True]
    assert [
        item.value
        for item in domains["query/mode"]
        if item.present
    ] == ["fast", "slow"]
    assert domains["query/limit"][0].value == 2
    assert {1, 5, 10}.issubset(
        {
            item.value
            for item in domains["query/limit"]
            if item.present
        }
    )
    assert domains["query/ratio"][0].value == 1.5
    assert [
        item.value
        for item in domains["query/ref"]
        if item.present
    ] == ["ref-a", "ref-b"]
    assert all(
        sum(item.has_value for item in domain) <= 8
        for domain in domains.values()
    )


def test_candidate_domain_samples_regex_generator_values() -> None:
    """Scenario: constraints can choose deterministic values from a regex domain."""
    from restscope.request_generation.models import InputGeneratorConfig
    from restscope.request_generation.constraint_solver import build_candidate_domains

    config = _solver_config()
    configs = [
        (
            InputGeneratorConfig(
                input_node_id=item.input_node_id,
                inclusion_probability=item.inclusion_probability,
                strategy={
                    "type": "regex",
                    "pattern": "^[A-Z]{4}$",
                    "min_length": 4,
                    "max_length": 4,
                },
            )
            if item.input_node_id == "query/mode"
            else item
        )
        for item in config.configs
    ]
    config = config.model_copy(update={"configs": configs})
    constraints = _constraint_set(
        {"type": "present", "input_node_id": "query/mode"},
    )

    domains = build_candidate_domains(
        operation=config.snapshot,
        config=config,
        constraints=constraints,
        baseline=_baseline(),
        run_seed=31,
        case_index=0,
        reference_values=_ReferenceValues(["ref-a"]),
    )

    generated = [
        item.value
        for item in domains["query/mode"]
        if item.present and item.has_value and item.value != "fast"
    ]
    assert generated
    assert all(
        isinstance(value, str) and re.fullmatch(r"[A-Z]{4}", value)
        for value in generated
    )


def test_solver_satisfies_implication_and_is_deterministic() -> None:
    """Scenario: verify that solver satisfies implication and is deterministic."""
    from restscope.request_generation.constraint_solver import solve_input_overrides

    config = _solver_config()
    constraints = _constraint_set(
        {"type": "present", "input_node_id": "query/mode"},
        {
            "type": "implies",
            "condition": {"type": "present", "input_node_id": "query/mode"},
            "consequence": {
                "type": "present",
                "input_node_id": "query/enabled",
            },
        }
    )
    kwargs = {
        "operation": config.snapshot,
        "config": config,
        "constraints": constraints,
        "baseline": _baseline(),
        "run_seed": 31,
        "case_index": 0,
        "reference_values": _ReferenceValues(["ref-a"]),
    }

    first = solve_input_overrides(**kwargs)
    second = solve_input_overrides(**kwargs)

    assert first == second
    assert first["query/enabled"].present is True


def test_solver_satisfies_arithmetic_relations() -> None:
    """Scenario: verify that solver satisfies arithmetic relations."""
    from restscope.request_generation.constraint_solver import solve_input_overrides

    config = _solver_config()
    constraints = _constraint_set(
        {
            "type": "compare",
            "operator": ">",
            "left": {"type": "input_value", "input_node_id": "query/limit"},
            "right": {"type": "input_value", "input_node_id": "query/offset"},
        }
    )

    overrides = solve_input_overrides(
        operation=config.snapshot,
        config=config,
        constraints=constraints,
        baseline=_baseline(),
        run_seed=31,
        case_index=0,
        reference_values=_ReferenceValues(["ref-a"]),
    )

    assert overrides["query/limit"].value > overrides["query/offset"].value


def test_solver_forces_structural_ancestors_present() -> None:
    """Scenario: verify that solver forces structural ancestors present."""
    from restscope.request_generation.constraint_solver import solve_input_overrides

    config = _solver_config()
    constraints = _constraint_set(
        {
            "type": "compare",
            "operator": "==",
            "left": {"type": "input_value", "input_node_id": "body/count"},
            "right": {"type": "literal", "value": 2},
        }
    )

    overrides = solve_input_overrides(
        operation=config.snapshot,
        config=config,
        constraints=constraints,
        baseline=_baseline(),
        run_seed=31,
        case_index=0,
        reference_values=_ReferenceValues(["ref-a"]),
    )

    assert overrides["body/count"].value == 2
    assert overrides["body/json"].present is True
    assert overrides["body"].present is True


def test_solver_reports_unsatisfiable_constraints() -> None:
    """Scenario: verify that solver reports unsatisfiable constraints."""
    from restscope.request_generation.constraint_solver import (
        ConstraintSolveError,
        solve_input_overrides,
    )

    config = _solver_config()
    constraints = _constraint_set(
        {
            "type": "compare",
            "operator": ">",
            "left": {"type": "input_value", "input_node_id": "query/fixed"},
            "right": {"type": "literal", "value": 10},
        }
    )

    with pytest.raises(ConstraintSolveError) as raised:
        solve_input_overrides(
            operation=config.snapshot,
            config=config,
            constraints=constraints,
            baseline=_baseline(),
            run_seed=31,
            case_index=0,
            reference_values=_ReferenceValues(["ref-a"]),
        )

    assert raised.value.code == "constraint_unsatisfiable"


def test_solver_reports_search_budget_exhaustion_before_later_solution() -> None:
    """Scenario: verify that solver reports search budget exhaustion before later solution."""
    from restscope.request_generation.constraint_solver import (
        ConstraintSolveError,
        solve_input_overrides,
    )

    config = _solver_config()
    constraints = _constraint_set(
        {
            "type": "not",
            "expression": {
                "type": "or",
                "expressions": [
                    {
                        "type": "present",
                        "input_node_id": "query/enabled",
                    },
                    {
                        "type": "present",
                        "input_node_id": "query/mode",
                    },
                ],
            },
        }
    )

    with pytest.raises(ConstraintSolveError) as raised:
        solve_input_overrides(
            operation=config.snapshot,
            config=config,
            constraints=constraints,
            baseline=_baseline(),
            run_seed=31,
            case_index=0,
            reference_values=_ReferenceValues(["ref-a"]),
            max_search_states=1,
        )

    assert raised.value.code == "constraint_search_exhausted"


def test_solver_prunes_definitively_false_partial_assignments() -> None:
    """Scenario: verify that solver prunes definitively false partial assignments."""
    from restscope.request_generation.constraint_solver import solve_input_overrides

    config = _solver_config()
    constraints = _constraint_set(
        {"type": "present", "input_node_id": "query/enabled"},
        {"type": "present", "input_node_id": "query/mode"},
    )

    overrides = solve_input_overrides(
        operation=config.snapshot,
        config=config,
        constraints=constraints,
        baseline=_baseline(),
        run_seed=31,
        case_index=0,
        reference_values=_ReferenceValues(["ref-a"]),
        max_search_states=1,
    )

    assert overrides["query/enabled"].present is True
    assert overrides["query/mode"].present is True


def test_candidate_domain_reports_an_empty_required_reference_pool() -> None:
    """Scenario: verify that candidate domain reports an empty required reference pool."""
    from restscope.request_generation.constraint_solver import (
        ConstraintSolveError,
        build_candidate_domains,
    )

    config = _solver_config()
    constraints = _constraint_set(
        {
            "type": "compare",
            "operator": "==",
            "left": {"type": "input_value", "input_node_id": "query/ref"},
            "right": {"type": "literal", "value": "ref-b"},
        }
    )
    baseline = _baseline().model_copy(
        update={
            "query_parameters": {
                key: value
                for key, value in _baseline().query_parameters.items()
                if key != "ref"
            },
            "generated_values": [
                item
                for item in _baseline().generated_values
                if item.input_node_id != "query/ref"
            ],
            "omitted_input_node_ids": [
                *_baseline().omitted_input_node_ids,
                "query/ref",
            ],
        }
    )

    with pytest.raises(ConstraintSolveError) as raised:
        build_candidate_domains(
            operation=config.snapshot,
            config=config,
            constraints=constraints,
            baseline=baseline,
            run_seed=31,
            case_index=0,
            reference_values=_ReferenceValues([]),
        )

    assert raised.value.code == "constraint_empty_domain"


def test_solver_contracts_are_exported_from_testing_package() -> None:
    """Scenario: verify that solver contracts are exported from testing package."""
    from restscope.request_generation.constraint_solver import (
        ConstraintSolveError,
        solve_input_overrides,
    )

    assert issubclass(ConstraintSolveError, ValueError)
    assert callable(solve_input_overrides)


def test_generated_case_assignments_recover_container_presence() -> None:
    """Scenario: verify that generated case assignments recover container presence."""
    from restscope.request_generation.models import (
        GeneratedTestCase,
        InputNodeSnapshot,
        OperationTestSnapshot,
        ParameterSnapshot,
        SchemaSnapshot,
    )
    from restscope.request_generation.constraint_solver import assignments_from_generated_case

    operation = OperationTestSnapshot(
        operation_key="POST /containers",
        method="POST",
        path="/containers",
        parameters=[
            ParameterSnapshot(
                input_node_id="query/filter",
                name="filter",
                location="query",
                required=False,
            )
        ],
        request_body_node_id="body",
        media_type_node_ids={"application/json": "body/json"},
        available_media_types=["application/json"],
        input_nodes=[
            InputNodeSnapshot(
                input_node_id="query/filter",
                node_kind="parameter",
                canonical_path="query/filter",
                required=False,
                schema_contract=SchemaSnapshot(type="object"),
            ),
            InputNodeSnapshot(
                input_node_id="body",
                node_kind="request_body",
                canonical_path="body",
                required=False,
            ),
            InputNodeSnapshot(
                input_node_id="body/json",
                node_kind="media_type",
                canonical_path="body/application~1json",
                parent_node_id="body",
                required=True,
                schema_contract=SchemaSnapshot(
                    type="object",
                    properties={"meta": SchemaSnapshot(type="object")},
                ),
            ),
            InputNodeSnapshot(
                input_node_id="body/meta",
                node_kind="property",
                canonical_path="body/application~1json/properties/meta",
                parent_node_id="body/json",
                required=False,
                schema_contract=SchemaSnapshot(type="object"),
            ),
        ],
    )
    generated = GeneratedTestCase(
        operation_key=operation.operation_key,
        case_index=0,
        media_type="application/json",
        path_parameters={},
        query_parameters={"filter": {}},
        header_parameters={},
        cookie_parameters={},
        body={"meta": {}},
        body_present=True,
        generated_values=[],
        omitted_input_node_ids=[],
    )

    assignments = assignments_from_generated_case(operation, generated)

    assert assignments["query/filter"].present is True
    assert assignments["body"].present is True
    assert assignments["body/json"].present is True
    assert assignments["body/meta"].present is True
