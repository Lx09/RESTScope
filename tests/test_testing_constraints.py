"""Regression scenarios for testing constraints. Each test documents one observable contract or failure boundary."""

from __future__ import annotations

import pytest
from pydantic import ValidationError


def _operation_snapshot():
    from restscope.testing import (
        InputNodeSnapshot,
        OperationTestSnapshot,
        SchemaSnapshot,
    )

    return OperationTestSnapshot(
        operation_key="POST /search",
        method="POST",
        path="/search",
        parameters=[],
        request_body_node_id="body",
        media_type_node_ids={"application/json": "body/json"},
        available_media_types=["application/json"],
        input_nodes=[
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
                required=False,
                schema_contract=SchemaSnapshot(type="integer"),
            ),
            InputNodeSnapshot(
                input_node_id="query/offset",
                node_kind="parameter",
                canonical_path="query/offset",
                required=False,
                schema_contract=SchemaSnapshot(type="integer"),
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
                    properties={
                        "count": SchemaSnapshot(type="integer"),
                        "tags": SchemaSnapshot(
                            type="array",
                            items=SchemaSnapshot(type="string"),
                        ),
                    },
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
            InputNodeSnapshot(
                input_node_id="body/tags",
                node_kind="property",
                canonical_path="body/application~1json/properties/tags",
                parent_node_id="body/json",
                required=False,
                schema_contract=SchemaSnapshot(
                    type="array",
                    items=SchemaSnapshot(type="string"),
                ),
            ),
            InputNodeSnapshot(
                input_node_id="body/tags/items",
                node_kind="items",
                canonical_path="body/application~1json/properties/tags/items",
                parent_node_id="body/tags",
                required=True,
                schema_contract=SchemaSnapshot(type="string"),
            ),
        ],
    )


def test_constraint_contracts_parse_recursive_expressions() -> None:
    """Scenario: verify that constraint contracts parse recursive expressions."""
    from restscope.testing.constraints import (
        ComparePredicate,
        ConstraintSet,
        ImplicationConstraint,
        InputValue,
        LiteralValue,
        PresentPredicate,
    )

    constraints = ConstraintSet(
        constraints=[
            ImplicationConstraint(
                type="implies",
                condition=PresentPredicate(
                    type="present",
                    input_node_id="query/mode",
                ),
                consequence=ComparePredicate(
                    type="compare",
                    operator="==",
                    left=InputValue(
                        type="input_value",
                        input_node_id="query/limit",
                    ),
                    right=LiteralValue(type="literal", value=10),
                ),
            )
        ]
    )

    implication = constraints.constraints[0]
    assert isinstance(implication, ImplicationConstraint)
    assert isinstance(implication.condition, PresentPredicate)
    assert isinstance(implication.consequence, ComparePredicate)
    assert isinstance(implication.consequence.left, InputValue)
    assert isinstance(implication.consequence.right, LiteralValue)


def test_constraint_contracts_are_frozen_and_forbid_extra_fields() -> None:
    """Scenario: verify that constraint contracts are frozen and forbid extra fields."""
    from restscope.testing.constraints import PresentPredicate

    predicate = PresentPredicate(
        type="present",
        input_node_id="query/mode",
    )

    with pytest.raises(ValidationError):
        predicate.input_node_id = "query/other"

    with pytest.raises(ValidationError):
        PresentPredicate.model_validate(
            {
                "type": "present",
                "input_node_id": "query/mode",
                "label": "Requires",
            }
        )


def test_assignment_distinguishes_omission_explicit_null_and_structural_presence() -> None:
    """Scenario: verify that assignment distinguishes omission explicit null and structural presence."""
    from restscope.testing.constraints import InputAssignment, InputNodeOverride

    omitted = InputAssignment(present=False)
    explicit_null = InputAssignment(
        present=True,
        has_value=True,
        value=None,
    )
    structural = InputNodeOverride(present=True, has_value=False)

    assert omitted.present is False
    assert omitted.has_value is False
    assert explicit_null.present is True
    assert explicit_null.has_value is True
    assert explicit_null.value is None
    assert structural.present is True
    assert structural.has_value is False


@pytest.mark.parametrize(
    "payload",
    [
        {"present": False, "has_value": True, "value": None},
        {"present": False, "has_value": False, "value": "leaked"},
        {"present": True, "has_value": False, "value": "leaked"},
    ],
)
def test_assignment_rejects_inconsistent_value_state(payload: dict) -> None:
    """Scenario: verify that assignment rejects inconsistent value state."""
    from restscope.testing.constraints import InputAssignment

    with pytest.raises(ValidationError):
        InputAssignment.model_validate(payload)


def test_constraint_set_rejects_an_empty_expression_list() -> None:
    """Scenario: verify that constraint set rejects an empty expression list."""
    from restscope.testing.constraints import ConstraintSet

    with pytest.raises(ValidationError):
        ConstraintSet(constraints=[])


def test_constraint_validation_accepts_supported_fixed_scalar_inputs() -> None:
    """Scenario: verify that constraint validation accepts supported fixed scalar inputs."""
    from restscope.testing.constraints import (
        ComparePredicate,
        ConstraintSet,
        InputValue,
        LiteralValue,
        PresentPredicate,
        validate_constraint_set,
    )

    constraints = ConstraintSet(
        constraints=[
            PresentPredicate(type="present", input_node_id="body"),
            ComparePredicate(
                type="compare",
                operator=">=",
                left=InputValue(
                    type="input_value",
                    input_node_id="body/count",
                ),
                right=LiteralValue(type="literal", value=1),
            ),
        ]
    )

    assert validate_constraint_set(constraints, _operation_snapshot()) is constraints


@pytest.mark.parametrize(
    ("expression", "error_code"),
    [
        (
            {
                "type": "present",
                "input_node_id": "query/unknown",
            },
            "constraint_unknown_input",
        ),
        (
            {
                "type": "compare",
                "operator": "==",
                "left": {
                    "type": "input_value",
                    "input_node_id": "body",
                },
                "right": {"type": "literal", "value": None},
            },
            "constraint_unsupported_input",
        ),
        (
            {
                "type": "compare",
                "operator": "==",
                "left": {
                    "type": "input_value",
                    "input_node_id": "body/tags/items",
                },
                "right": {"type": "literal", "value": "x"},
            },
            "constraint_repeated_input",
        ),
        (
            {
                "type": "matches",
                "value": {
                    "type": "input_value",
                    "input_node_id": "query/mode",
                },
                "pattern": "[",
            },
            "constraint_invalid_pattern",
        ),
        (
            {
                "type": "compare",
                "operator": ">",
                "left": {
                    "type": "input_value",
                    "input_node_id": "query/mode",
                },
                "right": {"type": "literal", "value": 2},
            },
            "constraint_incompatible_types",
        ),
    ],
)
def test_constraint_validation_rejects_invalid_references_and_types(
    expression: dict,
    error_code: str,
) -> None:
    """Scenario: verify that constraint validation rejects invalid references and types."""
    from restscope.testing.constraints import (
        ConstraintSet,
        ConstraintValidationError,
        validate_constraint_set,
    )

    constraints = ConstraintSet.model_validate({"constraints": [expression]})

    with pytest.raises(ConstraintValidationError) as raised:
        validate_constraint_set(constraints, _operation_snapshot())

    assert raised.value.code == error_code


def test_constraint_validation_rejects_inverted_cardinality_bounds() -> None:
    """Scenario: verify that constraint validation rejects inverted cardinality bounds."""
    from restscope.testing.constraints import (
        CardinalityConstraint,
        ConstraintSet,
        ConstraintValidationError,
        PresentPredicate,
        validate_constraint_set,
    )

    constraints = ConstraintSet(
        constraints=[
            CardinalityConstraint(
                type="cardinality",
                expressions=[
                    PresentPredicate(
                        type="present",
                        input_node_id="query/mode",
                    )
                ],
                minimum=1,
                maximum=0,
            )
        ]
    )

    with pytest.raises(ConstraintValidationError) as raised:
        validate_constraint_set(constraints, _operation_snapshot())

    assert raised.value.code == "constraint_invalid_cardinality"


def test_evaluation_distinguishes_absent_input_from_explicit_null() -> None:
    """Scenario: verify that evaluation distinguishes absent input from explicit null."""
    from restscope.testing.constraints import (
        ComparePredicate,
        ConstraintSet,
        InputAssignment,
        InputValue,
        LiteralValue,
        evaluate_constraint_set,
    )

    constraints = ConstraintSet(
        constraints=[
            ComparePredicate(
                type="compare",
                operator="==",
                left=InputValue(
                    type="input_value",
                    input_node_id="query/mode",
                ),
                right=LiteralValue(type="literal", value=None),
            )
        ]
    )

    assert (
        evaluate_constraint_set(
            constraints,
            {"query/mode": InputAssignment(present=False)},
        )
        is False
    )
    assert (
        evaluate_constraint_set(
            constraints,
            {
                "query/mode": InputAssignment(
                    present=True,
                    has_value=True,
                    value=None,
                )
            },
        )
        is True
    )


def test_evaluation_supports_nested_logic_cardinality_and_implication() -> None:
    """Scenario: verify that evaluation supports nested logic cardinality and implication."""
    from restscope.testing.constraints import (
        AndConstraint,
        CardinalityConstraint,
        ConstraintSet,
        ImplicationConstraint,
        InputAssignment,
        NotConstraint,
        OrConstraint,
        PresentPredicate,
        evaluate_constraint_set,
    )

    mode = PresentPredicate(type="present", input_node_id="query/mode")
    limit = PresentPredicate(type="present", input_node_id="query/limit")
    offset = PresentPredicate(type="present", input_node_id="query/offset")
    constraints = ConstraintSet(
        constraints=[
            AndConstraint(
                type="and",
                expressions=[
                    ImplicationConstraint(
                        type="implies",
                        condition=mode,
                        consequence=limit,
                    ),
                    CardinalityConstraint(
                        type="cardinality",
                        expressions=[limit, offset],
                        minimum=1,
                        maximum=1,
                    ),
                    OrConstraint(
                        type="or",
                        expressions=[
                            mode,
                            NotConstraint(type="not", expression=offset),
                        ],
                    ),
                ],
            )
        ]
    )

    assignments = {
        "query/mode": InputAssignment(present=True, has_value=True, value="fast"),
        "query/limit": InputAssignment(present=True, has_value=True, value=10),
        "query/offset": InputAssignment(present=False),
    }

    assert evaluate_constraint_set(constraints, assignments) is True


def test_evaluation_handles_arithmetic_matching_and_total_failures() -> None:
    """Scenario: verify that evaluation handles arithmetic matching and total failures."""
    from restscope.testing.constraints import (
        ArithmeticValue,
        ComparePredicate,
        ConstraintSet,
        InputAssignment,
        InputValue,
        LiteralValue,
        MatchesPredicate,
        evaluate_constraint_set,
    )

    assignments = {
        "query/mode": InputAssignment(
            present=True,
            has_value=True,
            value="fast-mode",
        ),
        "query/limit": InputAssignment(present=True, has_value=True, value=8),
        "query/offset": InputAssignment(present=True, has_value=True, value=2),
    }
    valid = ConstraintSet(
        constraints=[
            MatchesPredicate(
                type="matches",
                value=InputValue(
                    type="input_value",
                    input_node_id="query/mode",
                ),
                pattern=r"^fast",
            ),
            ComparePredicate(
                type="compare",
                operator=">",
                left=ArithmeticValue(
                    type="arithmetic",
                    operator="-",
                    left=InputValue(
                        type="input_value",
                        input_node_id="query/limit",
                    ),
                    right=InputValue(
                        type="input_value",
                        input_node_id="query/offset",
                    ),
                ),
                right=LiteralValue(type="literal", value=5),
            ),
        ]
    )
    divide_by_zero = ConstraintSet(
        constraints=[
            ComparePredicate(
                type="compare",
                operator="==",
                left=ArithmeticValue(
                    type="arithmetic",
                    operator="/",
                    left=LiteralValue(type="literal", value=1),
                    right=LiteralValue(type="literal", value=0),
                ),
                right=LiteralValue(type="literal", value=0),
            )
        ]
    )
    bool_arithmetic = ConstraintSet(
        constraints=[
            ComparePredicate(
                type="compare",
                operator="==",
                left=ArithmeticValue(
                    type="arithmetic",
                    operator="+",
                    left=LiteralValue(type="literal", value=True),
                    right=LiteralValue(type="literal", value=1),
                ),
                right=LiteralValue(type="literal", value=2),
            )
        ]
    )

    assert evaluate_constraint_set(valid, assignments) is True
    assert evaluate_constraint_set(divide_by_zero, assignments) is False
    assert evaluate_constraint_set(bool_arithmetic, assignments) is False


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        (
            {
                "type": "implies",
                "condition": {"type": "present", "input_node_id": "a"},
                "consequence": {"type": "present", "input_node_id": "b"},
            },
            "Requires",
        ),
        (
            {
                "type": "cardinality",
                "expressions": [
                    {"type": "present", "input_node_id": "a"},
                    {"type": "present", "input_node_id": "b"},
                ],
                "minimum": 1,
                "maximum": 2,
            },
            "Or",
        ),
        (
            {
                "type": "cardinality",
                "expressions": [
                    {"type": "present", "input_node_id": "a"},
                    {"type": "present", "input_node_id": "b"},
                ],
                "minimum": 1,
                "maximum": 1,
            },
            "OnlyOne",
        ),
        (
            {
                "type": "cardinality",
                "expressions": [
                    {"type": "present", "input_node_id": "a"},
                    {"type": "present", "input_node_id": "b"},
                ],
                "minimum": 0,
                "maximum": 1,
            },
            "ZeroOrOne",
        ),
        (
            {
                "type": "or",
                "expressions": [
                    {
                        "type": "cardinality",
                        "expressions": [
                            {"type": "present", "input_node_id": "a"},
                            {"type": "present", "input_node_id": "b"},
                        ],
                        "minimum": 0,
                        "maximum": 0,
                    },
                    {
                        "type": "cardinality",
                        "expressions": [
                            {"type": "present", "input_node_id": "b"},
                            {"type": "present", "input_node_id": "a"},
                        ],
                        "minimum": 2,
                        "maximum": 2,
                    },
                ],
            },
            "AllOrNone",
        ),
        (
            {
                "type": "compare",
                "operator": ">",
                "left": {"type": "input_value", "input_node_id": "a"},
                "right": {"type": "literal", "value": 0},
            },
            "Arithmetic/Relational",
        ),
        (
            {
                "type": "and",
                "expressions": [
                    {"type": "present", "input_node_id": "a"},
                    {"type": "present", "input_node_id": "b"},
                ],
            },
            "Complex",
        ),
    ],
)
def test_classification_is_derived_from_ast_shape(
    expression: dict,
    expected: str,
) -> None:
    """Scenario: verify that classification is derived from ast shape."""
    from restscope.testing.constraints import (
        ConstraintSet,
        classify_constraint,
    )

    parsed = ConstraintSet.model_validate({"constraints": [expression]})

    assert classify_constraint(parsed.constraints[0]) == expected


def test_normalization_orders_commutative_children() -> None:
    """Scenario: verify that normalization orders commutative children."""
    from restscope.testing.constraints import ConstraintSet, normalize_constraint_set

    first = ConstraintSet.model_validate(
        {
            "constraints": [
                {
                    "type": "and",
                    "expressions": [
                        {"type": "present", "input_node_id": "b"},
                        {"type": "present", "input_node_id": "a"},
                    ],
                }
            ]
        }
    )
    second = ConstraintSet.model_validate(
        {
            "constraints": [
                {
                    "type": "and",
                    "expressions": [
                        {"type": "present", "input_node_id": "a"},
                        {"type": "present", "input_node_id": "b"},
                    ],
                }
            ]
        }
    )

    assert normalize_constraint_set(first) == normalize_constraint_set(second)


def test_constraint_services_are_exported_from_testing_package() -> None:
    """Scenario: verify that constraint services are exported from testing package."""
    from restscope.testing import (
        ConstraintValidationError,
        classify_constraint,
        evaluate_constraint_set,
        normalize_constraint_set,
        referenced_input_node_ids,
        validate_constraint_set,
    )

    assert issubclass(ConstraintValidationError, ValueError)
    assert callable(classify_constraint)
    assert callable(evaluate_constraint_set)
    assert callable(normalize_constraint_set)
    assert callable(referenced_input_node_ids)
    assert callable(validate_constraint_set)
