"""Typed same-request parameter constraint contracts."""

from __future__ import annotations

from typing import Annotated, Any, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, model_validator


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


__all__ = [
    "AndConstraint",
    "ArithmeticValue",
    "BooleanExpression",
    "CardinalityConstraint",
    "ComparePredicate",
    "ConstraintSet",
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
]
