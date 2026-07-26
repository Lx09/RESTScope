"""Typed same-request parameter constraint contracts."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import Annotated, Any, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .models import InputNodeSnapshot, OperationTestSnapshot


class _ConstraintModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class InputAssignment(_ConstraintModel):
    """Presence and optional value for one frozen input node."""

    present: bool
    has_value: bool = False
    value: Any = None

    @model_validator(mode="after")
    def validate_value_state(self) -> "InputAssignment":
        if not self.present and self.has_value:
            raise ValueError("an absent input cannot have a value")
        if not self.has_value and self.value is not None:
            raise ValueError("value requires has_value=true")
        return self


class InputNodeOverride(InputAssignment):
    """A solver-selected presence/value override for generation."""


class InputValue(_ConstraintModel):
    type: Literal["input_value"]
    input_node_id: str = Field(min_length=1, max_length=1000)


class LiteralValue(_ConstraintModel):
    type: Literal["literal"]
    value: Any


class ArithmeticValue(_ConstraintModel):
    type: Literal["arithmetic"]
    operator: Literal["+", "-", "*", "/"]
    left: "ValueExpression"
    right: "ValueExpression"


class PresentPredicate(_ConstraintModel):
    type: Literal["present"]
    input_node_id: str = Field(min_length=1, max_length=1000)


class ComparePredicate(_ConstraintModel):
    type: Literal["compare"]
    operator: Literal["==", "!=", "<", "<=", ">", ">="]
    left: "ValueExpression"
    right: "ValueExpression"


class MatchesPredicate(_ConstraintModel):
    type: Literal["matches"]
    value: "ValueExpression"
    pattern: str = Field(max_length=2000)


class ImplicationConstraint(_ConstraintModel):
    type: Literal["implies"]
    condition: "BooleanExpression"
    consequence: "BooleanExpression"


class CardinalityConstraint(_ConstraintModel):
    type: Literal["cardinality"]
    expressions: list["BooleanExpression"] = Field(min_length=1, max_length=100)
    minimum: int = Field(ge=0)
    maximum: int = Field(ge=0)


class AndConstraint(_ConstraintModel):
    type: Literal["and"]
    expressions: list["BooleanExpression"] = Field(min_length=1, max_length=100)


class OrConstraint(_ConstraintModel):
    type: Literal["or"]
    expressions: list["BooleanExpression"] = Field(min_length=1, max_length=100)


class NotConstraint(_ConstraintModel):
    type: Literal["not"]
    expression: "BooleanExpression"


ValueExpression: TypeAlias = Annotated[
    InputValue | LiteralValue | ArithmeticValue,
    Field(discriminator="type"),
]

BooleanExpression: TypeAlias = Annotated[
    PresentPredicate
    | ComparePredicate
    | MatchesPredicate
    | ImplicationConstraint
    | CardinalityConstraint
    | AndConstraint
    | OrConstraint
    | NotConstraint,
    Field(discriminator="type"),
]


class ConstraintSet(_ConstraintModel):
    constraints: list[BooleanExpression] = Field(min_length=1, max_length=20)


_RECURSIVE_MODELS = (
    ArithmeticValue,
    ComparePredicate,
    MatchesPredicate,
    ImplicationConstraint,
    CardinalityConstraint,
    AndConstraint,
    OrConstraint,
    NotConstraint,
    ConstraintSet,
)

for _model in _RECURSIVE_MODELS:
    _model.model_rebuild(_types_namespace=globals())


ConstraintKind: TypeAlias = Literal[
    "Requires",
    "Or",
    "OnlyOne",
    "AllOrNone",
    "ZeroOrOne",
    "Arithmetic/Relational",
    "Complex",
]


class ConstraintValidationError(ValueError):
    """A constraint is incompatible with the frozen operation contract."""

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


def validate_constraint_set(
    constraints: ConstraintSet,
    operation: OperationTestSnapshot,
) -> ConstraintSet:
    """Validate all references and typed operations against one snapshot."""

    nodes = {node.input_node_id: node for node in operation.input_nodes}
    for expression in constraints.constraints:
        _validate_boolean(expression, nodes)
    return constraints


def normalize_constraint_set(constraints: ConstraintSet) -> ConstraintSet:
    """Return a stable representation for equality and identity derivation."""

    normalized = [_normalize_boolean(item) for item in constraints.constraints]
    normalized.sort(key=_canonical_key)
    return ConstraintSet(constraints=normalized)


def classify_constraint(expression: BooleanExpression) -> ConstraintKind:
    """Derive a RESTest-style label from normalized AST shape."""

    normalized = _normalize_boolean(expression)
    if isinstance(normalized, ImplicationConstraint) and isinstance(
        normalized.condition,
        PresentPredicate,
    ) and isinstance(normalized.consequence, PresentPredicate):
        return "Requires"
    if isinstance(normalized, CardinalityConstraint):
        count = len(normalized.expressions)
        if normalized.minimum == normalized.maximum == 1:
            return "OnlyOne"
        if normalized.minimum == 0 and normalized.maximum == 1:
            return "ZeroOrOne"
        if normalized.minimum == 1 and normalized.maximum == count:
            return "Or"
    if _is_all_or_none(normalized):
        return "AllOrNone"
    if isinstance(normalized, ComparePredicate):
        return "Arithmetic/Relational"
    return "Complex"


def evaluate_constraint_set(
    constraints: ConstraintSet,
    assignments: Mapping[str, InputAssignment],
) -> bool:
    """Evaluate all constraints without leaking operand runtime errors."""

    return all(_evaluate_boolean(item, assignments) for item in constraints.constraints)


def evaluate_constraint_set_partial(
    constraints: ConstraintSet,
    assignments: Mapping[str, InputAssignment],
) -> bool | None:
    """Evaluate known inputs and return None while the result is undecidable."""

    results = [
        _evaluate_boolean_partial(item, assignments)
        for item in constraints.constraints
    ]
    if any(result is False for result in results):
        return False
    if all(result is True for result in results):
        return True
    return None


def referenced_input_node_ids(constraints: ConstraintSet) -> tuple[str, ...]:
    """Return stable first-seen input references from one constraint set."""

    result: list[str] = []
    for expression in constraints.constraints:
        for node_id in _boolean_references(expression):
            if node_id not in result:
                result.append(node_id)
    return tuple(result)


def _validate_boolean(
    expression: BooleanExpression,
    nodes: Mapping[str, InputNodeSnapshot],
) -> None:
    if isinstance(expression, PresentPredicate):
        _validate_input_reference(expression.input_node_id, nodes, for_value=False)
        return
    if isinstance(expression, ComparePredicate):
        left_types = _validate_value(expression.left, nodes)
        right_types = _validate_value(expression.right, nodes)
        if not _comparison_types_compatible(
            expression.operator,
            left_types,
            right_types,
        ):
            raise ConstraintValidationError(
                "constraint_incompatible_types",
                f"Incompatible operands for {expression.operator}",
                input_node_ids=tuple(_value_references(expression.left))
                + tuple(_value_references(expression.right)),
            )
        return
    if isinstance(expression, MatchesPredicate):
        value_types = _validate_value(expression.value, nodes)
        if "unknown" not in value_types and "string" not in value_types:
            raise ConstraintValidationError(
                "constraint_incompatible_types",
                "Regular-expression matching requires a string value",
                input_node_ids=tuple(_value_references(expression.value)),
            )
        try:
            re.compile(expression.pattern)
        except re.error as exc:
            raise ConstraintValidationError(
                "constraint_invalid_pattern",
                f"Invalid regular expression: {exc}",
                input_node_ids=tuple(_value_references(expression.value)),
            ) from exc
        return
    if isinstance(expression, ImplicationConstraint):
        _validate_boolean(expression.condition, nodes)
        _validate_boolean(expression.consequence, nodes)
        return
    if isinstance(expression, CardinalityConstraint):
        if (
            expression.minimum > expression.maximum
            or expression.maximum > len(expression.expressions)
        ):
            raise ConstraintValidationError(
                "constraint_invalid_cardinality",
                "Cardinality bounds must fit the expression count",
            )
        for item in expression.expressions:
            _validate_boolean(item, nodes)
        return
    if isinstance(expression, AndConstraint | OrConstraint):
        for item in expression.expressions:
            _validate_boolean(item, nodes)
        return
    if isinstance(expression, NotConstraint):
        _validate_boolean(expression.expression, nodes)
        return
    raise TypeError(f"Unsupported boolean expression: {type(expression).__name__}")


def _validate_value(
    expression: ValueExpression,
    nodes: Mapping[str, InputNodeSnapshot],
) -> frozenset[str]:
    if isinstance(expression, InputValue):
        node = _validate_input_reference(
            expression.input_node_id,
            nodes,
            for_value=True,
        )
        return _node_scalar_types(node)
    if isinstance(expression, LiteralValue):
        return frozenset({_literal_type(expression.value)})
    if isinstance(expression, ArithmeticValue):
        left_types = _validate_value(expression.left, nodes)
        right_types = _validate_value(expression.right, nodes)
        if not _numeric_types(left_types) or not _numeric_types(right_types):
            raise ConstraintValidationError(
                "constraint_incompatible_types",
                "Arithmetic requires numeric operands",
                input_node_ids=tuple(_value_references(expression)),
            )
        return frozenset({"number"})
    raise TypeError(f"Unsupported value expression: {type(expression).__name__}")


def _validate_input_reference(
    input_node_id: str,
    nodes: Mapping[str, InputNodeSnapshot],
    *,
    for_value: bool,
) -> InputNodeSnapshot:
    node = nodes.get(input_node_id)
    if node is None:
        raise ConstraintValidationError(
            "constraint_unknown_input",
            f"Unknown input node: {input_node_id}",
            input_node_ids=(input_node_id,),
        )
    current: InputNodeSnapshot | None = node
    while current is not None:
        if current.node_kind == "items":
            raise ConstraintValidationError(
                "constraint_repeated_input",
                f"Repeated array input is unsupported: {input_node_id}",
                input_node_ids=(input_node_id,),
            )
        current = (
            nodes.get(current.parent_node_id)
            if current.parent_node_id is not None
            else None
        )
    if for_value and not _is_scalar_node(node):
        raise ConstraintValidationError(
            "constraint_unsupported_input",
            f"Input node has no fixed scalar value: {input_node_id}",
            input_node_ids=(input_node_id,),
        )
    return node


def _is_scalar_node(node: InputNodeSnapshot) -> bool:
    schema = node.schema_contract
    if schema is None or node.node_kind == "request_body":
        return False
    declared = {schema.type} if isinstance(schema.type, str) else set(schema.type or ())
    return not (
        schema.properties
        or schema.items is not None
        or schema.all_of
        or schema.any_of
        or schema.one_of
        or "object" in declared
        or "array" in declared
    )


def _node_scalar_types(node: InputNodeSnapshot) -> frozenset[str]:
    schema = node.schema_contract
    if schema is None:
        return frozenset({"unknown"})
    declared = {schema.type} if isinstance(schema.type, str) else set(schema.type or ())
    result: set[str] = set()
    if declared.intersection({"integer", "number"}):
        result.add("number")
    if "string" in declared:
        result.add("string")
    if "boolean" in declared:
        result.add("boolean")
    if "null" in declared or schema.nullable:
        result.add("null")
    return frozenset(result or {"unknown"})


def _literal_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int | float):
        return "number"
    if isinstance(value, str):
        return "string"
    return "unsupported"


def _numeric_types(types: frozenset[str]) -> bool:
    return "unknown" in types or bool(types.intersection({"number"}))


def _comparison_types_compatible(
    operator: str,
    left: frozenset[str],
    right: frozenset[str],
) -> bool:
    if "unsupported" in left or "unsupported" in right:
        return False
    if "unknown" in left or "unknown" in right:
        return True
    if operator in {"==", "!="}:
        return bool(left.intersection(right)) or (
            "number" in left and "number" in right
        )
    return bool(
        left.intersection(right).intersection({"number", "string"})
    )


def _normalize_boolean(expression: BooleanExpression) -> BooleanExpression:
    if isinstance(expression, ComparePredicate):
        left = _normalize_value(expression.left)
        right = _normalize_value(expression.right)
        if expression.operator in {"==", "!="} and _canonical_key(left) > _canonical_key(right):
            left, right = right, left
        return expression.model_copy(update={"left": left, "right": right})
    if isinstance(expression, MatchesPredicate):
        return expression.model_copy(
            update={"value": _normalize_value(expression.value)}
        )
    if isinstance(expression, ImplicationConstraint):
        return expression.model_copy(
            update={
                "condition": _normalize_boolean(expression.condition),
                "consequence": _normalize_boolean(expression.consequence),
            }
        )
    if isinstance(expression, CardinalityConstraint | AndConstraint | OrConstraint):
        children = [_normalize_boolean(item) for item in expression.expressions]
        children.sort(key=_canonical_key)
        return expression.model_copy(update={"expressions": children})
    if isinstance(expression, NotConstraint):
        return expression.model_copy(
            update={"expression": _normalize_boolean(expression.expression)}
        )
    return expression


def _normalize_value(expression: ValueExpression) -> ValueExpression:
    if not isinstance(expression, ArithmeticValue):
        return expression
    left = _normalize_value(expression.left)
    right = _normalize_value(expression.right)
    if expression.operator in {"+", "*"} and _canonical_key(left) > _canonical_key(right):
        left, right = right, left
    return expression.model_copy(update={"left": left, "right": right})


def _canonical_key(value: BaseModel) -> str:
    return json.dumps(
        value.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def _is_all_or_none(expression: BooleanExpression) -> bool:
    if not isinstance(expression, OrConstraint) or len(expression.expressions) != 2:
        return False
    first, second = expression.expressions
    if not isinstance(first, CardinalityConstraint) or not isinstance(
        second,
        CardinalityConstraint,
    ):
        return False
    first_count = len(first.expressions)
    second_count = len(second.expressions)
    if first_count != second_count or first_count < 2:
        return False
    first_keys = [_canonical_key(item) for item in first.expressions]
    second_keys = [_canonical_key(item) for item in second.expressions]
    if first_keys != second_keys:
        return False
    bounds = {
        (first.minimum, first.maximum),
        (second.minimum, second.maximum),
    }
    return bounds == {(0, 0), (first_count, first_count)}


_UNAVAILABLE = object()
_PARTIAL_UNKNOWN = object()


def _evaluate_boolean(
    expression: BooleanExpression,
    assignments: Mapping[str, InputAssignment],
) -> bool:
    if isinstance(expression, PresentPredicate):
        assignment = assignments.get(expression.input_node_id)
        return assignment.present if assignment is not None else False
    if isinstance(expression, ComparePredicate):
        left = _evaluate_value(expression.left, assignments)
        right = _evaluate_value(expression.right, assignments)
        if left is _UNAVAILABLE or right is _UNAVAILABLE:
            return False
        return _compare_values(expression.operator, left, right)
    if isinstance(expression, MatchesPredicate):
        value = _evaluate_value(expression.value, assignments)
        if not isinstance(value, str):
            return False
        try:
            return re.search(expression.pattern, value) is not None
        except re.error:
            return False
    if isinstance(expression, ImplicationConstraint):
        return not _evaluate_boolean(
            expression.condition,
            assignments,
        ) or _evaluate_boolean(expression.consequence, assignments)
    if isinstance(expression, CardinalityConstraint):
        count = sum(
            _evaluate_boolean(item, assignments)
            for item in expression.expressions
        )
        return expression.minimum <= count <= expression.maximum
    if isinstance(expression, AndConstraint):
        return all(
            _evaluate_boolean(item, assignments)
            for item in expression.expressions
        )
    if isinstance(expression, OrConstraint):
        return any(
            _evaluate_boolean(item, assignments)
            for item in expression.expressions
        )
    if isinstance(expression, NotConstraint):
        return not _evaluate_boolean(expression.expression, assignments)
    return False


def _evaluate_value(
    expression: ValueExpression,
    assignments: Mapping[str, InputAssignment],
) -> Any:
    if isinstance(expression, InputValue):
        assignment = assignments.get(expression.input_node_id)
        if assignment is None or not assignment.present or not assignment.has_value:
            return _UNAVAILABLE
        return assignment.value
    if isinstance(expression, LiteralValue):
        return expression.value
    if isinstance(expression, ArithmeticValue):
        left = _evaluate_value(expression.left, assignments)
        right = _evaluate_value(expression.right, assignments)
        if (
            left is _UNAVAILABLE
            or right is _UNAVAILABLE
            or not _is_number(left)
            or not _is_number(right)
        ):
            return _UNAVAILABLE
        try:
            if expression.operator == "+":
                return left + right
            if expression.operator == "-":
                return left - right
            if expression.operator == "*":
                return left * right
            if right == 0:
                return _UNAVAILABLE
            return left / right
        except (ArithmeticError, TypeError, ValueError):
            return _UNAVAILABLE
    return _UNAVAILABLE


def _evaluate_boolean_partial(
    expression: BooleanExpression,
    assignments: Mapping[str, InputAssignment],
) -> bool | None:
    if isinstance(expression, PresentPredicate):
        assignment = assignments.get(expression.input_node_id)
        return assignment.present if assignment is not None else None
    if isinstance(expression, ComparePredicate):
        left = _evaluate_value_partial(expression.left, assignments)
        right = _evaluate_value_partial(expression.right, assignments)
        if left is _PARTIAL_UNKNOWN or right is _PARTIAL_UNKNOWN:
            return None
        if left is _UNAVAILABLE or right is _UNAVAILABLE:
            return False
        return _compare_values(expression.operator, left, right)
    if isinstance(expression, MatchesPredicate):
        value = _evaluate_value_partial(expression.value, assignments)
        if value is _PARTIAL_UNKNOWN:
            return None
        if not isinstance(value, str):
            return False
        try:
            return re.search(expression.pattern, value) is not None
        except re.error:
            return False
    if isinstance(expression, ImplicationConstraint):
        condition = _evaluate_boolean_partial(expression.condition, assignments)
        consequence = _evaluate_boolean_partial(
            expression.consequence,
            assignments,
        )
        if condition is False or consequence is True:
            return True
        if condition is True:
            return consequence
        return None
    if isinstance(expression, CardinalityConstraint):
        results = [
            _evaluate_boolean_partial(item, assignments)
            for item in expression.expressions
        ]
        true_count = sum(result is True for result in results)
        unknown_count = sum(result is None for result in results)
        if true_count > expression.maximum:
            return False
        if true_count + unknown_count < expression.minimum:
            return False
        if unknown_count == 0:
            return expression.minimum <= true_count <= expression.maximum
        if (
            expression.minimum <= true_count
            and true_count + unknown_count <= expression.maximum
        ):
            return True
        return None
    if isinstance(expression, AndConstraint):
        results = [
            _evaluate_boolean_partial(item, assignments)
            for item in expression.expressions
        ]
        if any(result is False for result in results):
            return False
        if all(result is True for result in results):
            return True
        return None
    if isinstance(expression, OrConstraint):
        results = [
            _evaluate_boolean_partial(item, assignments)
            for item in expression.expressions
        ]
        if any(result is True for result in results):
            return True
        if all(result is False for result in results):
            return False
        return None
    if isinstance(expression, NotConstraint):
        result = _evaluate_boolean_partial(expression.expression, assignments)
        return None if result is None else not result
    return False


def _evaluate_value_partial(
    expression: ValueExpression,
    assignments: Mapping[str, InputAssignment],
) -> Any:
    if isinstance(expression, InputValue):
        assignment = assignments.get(expression.input_node_id)
        if assignment is None:
            return _PARTIAL_UNKNOWN
        if not assignment.present or not assignment.has_value:
            return _UNAVAILABLE
        return assignment.value
    if isinstance(expression, LiteralValue):
        return expression.value
    if isinstance(expression, ArithmeticValue):
        left = _evaluate_value_partial(expression.left, assignments)
        right = _evaluate_value_partial(expression.right, assignments)
        if left is _PARTIAL_UNKNOWN or right is _PARTIAL_UNKNOWN:
            return _PARTIAL_UNKNOWN
        if (
            left is _UNAVAILABLE
            or right is _UNAVAILABLE
            or not _is_number(left)
            or not _is_number(right)
        ):
            return _UNAVAILABLE
        try:
            if expression.operator == "+":
                return left + right
            if expression.operator == "-":
                return left - right
            if expression.operator == "*":
                return left * right
            if right == 0:
                return _UNAVAILABLE
            return left / right
        except (ArithmeticError, TypeError, ValueError):
            return _UNAVAILABLE
    return _UNAVAILABLE


def _compare_values(operator: str, left: Any, right: Any) -> bool:
    try:
        if operator == "==":
            return _values_equal(left, right)
        if operator == "!=":
            return not _values_equal(left, right)
        if not _ordered_values_compatible(left, right):
            return False
        if operator == "<":
            return left < right
        if operator == "<=":
            return left <= right
        if operator == ">":
            return left > right
        if operator == ">=":
            return left >= right
    except (ArithmeticError, TypeError, ValueError):
        return False
    return False


def _values_equal(left: Any, right: Any) -> bool:
    if _is_number(left) and _is_number(right):
        return left == right
    return type(left) is type(right) and left == right


def _ordered_values_compatible(left: Any, right: Any) -> bool:
    return (_is_number(left) and _is_number(right)) or (
        isinstance(left, str) and isinstance(right, str)
    )


def _is_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _boolean_references(expression: BooleanExpression) -> tuple[str, ...]:
    if isinstance(expression, PresentPredicate):
        return (expression.input_node_id,)
    if isinstance(expression, ComparePredicate):
        return (
            *_value_references(expression.left),
            *_value_references(expression.right),
        )
    if isinstance(expression, MatchesPredicate):
        return _value_references(expression.value)
    if isinstance(expression, ImplicationConstraint):
        return (
            *_boolean_references(expression.condition),
            *_boolean_references(expression.consequence),
        )
    if isinstance(expression, CardinalityConstraint | AndConstraint | OrConstraint):
        return tuple(
            node_id
            for item in expression.expressions
            for node_id in _boolean_references(item)
        )
    if isinstance(expression, NotConstraint):
        return _boolean_references(expression.expression)
    return ()


def _value_references(expression: ValueExpression) -> tuple[str, ...]:
    if isinstance(expression, InputValue):
        return (expression.input_node_id,)
    if isinstance(expression, ArithmeticValue):
        return (
            *_value_references(expression.left),
            *_value_references(expression.right),
        )
    return ()


__all__ = [
    "AndConstraint",
    "ArithmeticValue",
    "BooleanExpression",
    "CardinalityConstraint",
    "ComparePredicate",
    "ConstraintKind",
    "ConstraintSet",
    "ConstraintValidationError",
    "ImplicationConstraint",
    "InputAssignment",
    "InputNodeOverride",
    "InputValue",
    "LiteralValue",
    "MatchesPredicate",
    "NotConstraint",
    "OrConstraint",
    "PresentPredicate",
    "ValueExpression",
    "classify_constraint",
    "evaluate_constraint_set",
    "evaluate_constraint_set_partial",
    "normalize_constraint_set",
    "referenced_input_node_ids",
    "validate_constraint_set",
]
