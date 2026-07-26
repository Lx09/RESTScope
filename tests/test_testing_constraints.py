from __future__ import annotations

import pytest
from pydantic import ValidationError


def test_constraint_contracts_parse_recursive_expressions() -> None:
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
    from restscope.testing.constraints import InputAssignment

    with pytest.raises(ValidationError):
        InputAssignment.model_validate(payload)


def test_constraint_set_rejects_an_empty_expression_list() -> None:
    from restscope.testing.constraints import ConstraintSet

    with pytest.raises(ValidationError):
        ConstraintSet(constraints=[])
