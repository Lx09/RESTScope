"""Define semantic and compiled contracts for Parameter Patch Tools.

Agents submit semantic request-input handles and complete Generator/Constraint
replacements. The validation Module compiles those values into internal input
nodes, while Tool results project only semantic names back to the model.
"""

from __future__ import annotations

from typing import Annotated, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..constraints import ConstraintSet
from ..models import (
    ArrayGenerator,
    BooleanGenerator,
    ChoiceGenerator,
    ConstantGenerator,
    FormatGenerator,
    IntegerRangeGenerator,
    NumberRangeGenerator,
    RandomStringGenerator,
    RegexGenerator,
    ResourceIdentifierGenerator,
    InputGeneratorPatch,
    VariantGenerator,
)


class _Model(BaseModel):
    """Use frozen strict DTOs for model and Agent handoffs."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class SelectedReferenceProvenance(_Model):
    """Internal provenance for one selected, currently populated reference."""

    input_node_id: str = Field(min_length=1, max_length=1000)
    kind: Literal["resource_identifier", "response_value"]
    canonical_resource: str | None = Field(default=None, max_length=200)
    identifier: str | None = Field(default=None, max_length=200)
    component: str | None = Field(default=None, max_length=200)
    value_name: str | None = Field(default=None, max_length=200)
    compatible_scalar_type: str | None = Field(default=None, max_length=50)
    value_count: int = Field(ge=1)
    producer_operation_keys: list[str] = Field(default_factory=list, max_length=100)
    producer_status_code: str | None = Field(default=None, max_length=20)
    producer_media_type: str | None = Field(default=None, max_length=200)
    source_field: str | None = Field(default=None, max_length=1000)
    source_selector: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def validate_source(self) -> "SelectedReferenceProvenance":
        """Require the source fields appropriate to the selected reference kind."""
        if self.kind == "resource_identifier":
            if (
                not self.canonical_resource
                or not self.identifier
                or not self.component
                or self.value_name is not None
                or self.producer_operation_keys
                or self.producer_status_code is not None
                or self.producer_media_type is not None
                or self.source_field is not None
                or self.source_selector is not None
            ):
                raise ValueError(
                    "resource_identifier requires only canonical provenance"
                )
        elif (
            not self.value_name
            or self.canonical_resource is not None
            or self.identifier is not None
            or self.component is not None
            or len(self.producer_operation_keys) != 1
            or not self.producer_status_code
            or not self.producer_media_type
            or not self.source_field
            or not self.source_selector
        ):
            raise ValueError("response_value requires one complete producer source")
        return self


# Reuse Testing's validation where model and runtime meanings are identical.
# Response values keep a separate model-facing source because their persisted
# pool name belongs to deterministic runtime, not to the Agent.
class SemanticResponseValueSource(_Model):
    """Name one observed producer field without exposing its internal pool.

    The four fields copy the globally unique response-field identity returned
    by ``openapi.find_observed_response_fields``. Compilation later verifies
    the source against this Patch session and derives the consumer-owned
    ``value_name`` owned by the API Behavior Monitor.
    """

    operation_key: str = Field(min_length=1, max_length=1000)
    matched_status_code: str = Field(min_length=1, max_length=20)
    media_type: str = Field(min_length=1, max_length=200)
    field: str = Field(min_length=1, max_length=1000)


class SemanticResponseValueGenerator(_Model):
    """Select values from one observed response field chosen through a tool."""

    type: Literal["response_value"]
    source: SemanticResponseValueSource


SemanticGeneratorStrategy = Annotated[
    ConstantGenerator
    | ChoiceGenerator
    | IntegerRangeGenerator
    | NumberRangeGenerator
    | RandomStringGenerator
    | RegexGenerator
    | BooleanGenerator
    | FormatGenerator
    | ArrayGenerator
    | VariantGenerator
    | ResourceIdentifierGenerator
    | SemanticResponseValueGenerator,
    Field(discriminator="type"),
]


class SemanticInputValue(_Model):
    """Read one affected request input as a value inside a Constraint.

    ``input`` is the semantic handle shown to the model. Compilation later
    replaces it with the frozen input-node identity used by request generation.
    """

    type: Literal["input_value"]
    input: str = Field(min_length=1, max_length=1000)


class SemanticLiteralValue(_Model):
    """Use one JSON literal inside a comparison or arithmetic expression.

    ``value`` remains typed JSON data; no text parsing or runtime state change
    occurs while this proposal DTO is validated.
    """

    type: Literal["literal"]
    value: object


class SemanticArithmeticValue(_Model):
    """Combine two numeric values before another Constraint compares them.

    ``operator`` selects one supported arithmetic operation. ``left`` and
    ``right`` may recursively read an input, use a literal, or calculate a value.
    Executable validation later rejects incompatible operand types.
    """

    type: Literal["arithmetic"]
    operator: Literal["+", "-", "*", "/"]
    left: "SemanticValueExpression"
    right: "SemanticValueExpression"


SemanticValueExpression: TypeAlias = Annotated[
    SemanticInputValue | SemanticLiteralValue | SemanticArithmeticValue,
    Field(discriminator="type"),
]


class SemanticPresentPredicate(_Model):
    """Test whether one affected request input is included in a generated case.

    ``input`` must be a semantic handle in the current Patch requirement;
    compilation rejects unknown or out-of-scope handles.
    """

    type: Literal["present"]
    input: str = Field(min_length=1, max_length=1000)


class SemanticComparePredicate(_Model):
    """Compare two typed values using one equality or ordering rule.

    ``left`` and ``right`` use the recursive value DSL. Executable validation
    later rejects ordered comparisons whose operand types are incompatible.
    """

    type: Literal["compare"]
    operator: Literal["==", "!=", "<", "<=", ">", ">="]
    left: SemanticValueExpression
    right: SemanticValueExpression


class SemanticMatchesPredicate(_Model):
    """Require a string-compatible value to match a regular expression.

    ``pattern`` is bounded in size at the model Interface. Executable validation
    later confirms that ``value`` can produce strings.
    """

    type: Literal["matches"]
    value: SemanticValueExpression
    pattern: str = Field(max_length=2000)


class SemanticImplicationConstraint(_Model):
    """Require one consequence whenever its Boolean condition is true.

    Both fields recursively accept any Boolean DSL node, which lets a proposal
    express presence and value relationships without string expressions.
    """

    type: Literal["implies"]
    condition: "SemanticBooleanExpression"
    consequence: "SemanticBooleanExpression"


class SemanticCardinalityConstraint(_Model):
    """Bound how many expressions in one related group may be true.

    ``expressions`` is the group; ``minimum`` and ``maximum`` are inclusive
    true-count bounds. Executable validation rejects impossible bounds.
    """

    type: Literal["cardinality"]
    expressions: list["SemanticBooleanExpression"] = Field(
        min_length=1,
        max_length=100,
    )
    minimum: int = Field(ge=0)
    maximum: int = Field(ge=0)


class SemanticAndConstraint(_Model):
    """Require every expression in a non-empty recursive group to be true.

    The child field is always named ``expressions``; ``conditions`` is rejected
    as an extra input at the model Interface.
    """

    type: Literal["and"]
    expressions: list["SemanticBooleanExpression"] = Field(
        min_length=1,
        max_length=100,
    )


class SemanticOrConstraint(_Model):
    """Require at least one expression in a non-empty recursive group to be true.

    Like ``and``, this node uses the plural ``expressions`` field and forbids
    undeclared alternatives such as ``conditions`` or ``children``.
    """

    type: Literal["or"]
    expressions: list["SemanticBooleanExpression"] = Field(
        min_length=1,
        max_length=100,
    )


class SemanticNotConstraint(_Model):
    """Invert one nested Boolean expression.

    Unlike grouped nodes, ``not`` owns exactly one singular ``expression``.
    """

    type: Literal["not"]
    expression: "SemanticBooleanExpression"


SemanticBooleanExpression: TypeAlias = Annotated[
    SemanticPresentPredicate
    | SemanticComparePredicate
    | SemanticMatchesPredicate
    | SemanticImplicationConstraint
    | SemanticCardinalityConstraint
    | SemanticAndConstraint
    | SemanticOrConstraint
    | SemanticNotConstraint,
    Field(discriminator="type"),
]


# Resolve the quoted recursive annotations only after both unions exist. This
# keeps one finite generated JSON Schema while allowing Boolean nodes to nest.
for _recursive_model in (
    SemanticArithmeticValue,
    SemanticImplicationConstraint,
    SemanticCardinalityConstraint,
    SemanticAndConstraint,
    SemanticOrConstraint,
    SemanticNotConstraint,
):
    _recursive_model.model_rebuild(_types_namespace=globals())


class SemanticGeneratorChange(_Model):
    """One complete final Generator state using a semantic input path."""

    input: str = Field(min_length=1, max_length=1000)
    inclusion_probability: float = Field(ge=0, le=1)
    strategy: SemanticGeneratorStrategy


class SemanticConstraintChange(_Model):
    """One recursive semantic Constraint expression."""

    expression: SemanticBooleanExpression


class SemanticParameterPatch(_Model):
    """One complete semantic Generator and Constraint replacement.

    An empty ``constraints`` list is meaningful: it removes every active
    Constraint in the transitive closure selected by ``affected_inputs`` at the
    Tool interface. Unrelated Constraints are retained deterministically.
    """

    changes: list[SemanticGeneratorChange] = Field(default_factory=list, max_length=20)
    constraints: list[SemanticConstraintChange] = Field(
        default_factory=list,
        max_length=20,
    )

class CompiledConstraintPatch(_Model):
    """One stable, executable Constraint owned by the current requirement."""

    constraint_id: str = Field(min_length=1, max_length=100)
    kind: str = Field(min_length=1, max_length=100)
    constraint: ConstraintSet


class CompiledParameterPatch(_Model):
    """Complete executable Generator and Constraint replacement."""

    updates: list[InputGeneratorPatch] = Field(default_factory=list, max_length=100)
    constraints: list[CompiledConstraintPatch] = Field(
        default_factory=list,
        max_length=20,
    )
    selected_reference_provenance: list[SelectedReferenceProvenance] = Field(
        default_factory=list,
        max_length=100,
    )

    @model_validator(mode="after")
    def validate_patch(self) -> "CompiledParameterPatch":
        """Reject empty, duplicate, or mismatched reference-backed content."""
        changed = [item.input_node_id for item in self.updates]
        if len(changed) != len(set(changed)):
            raise ValueError("each input may be changed at most once")
        selected = [
            item.input_node_id for item in self.selected_reference_provenance
        ]
        if len(selected) != len(set(selected)):
            raise ValueError("each input may select at most one reference option")
        if not set(selected).issubset(set(changed)):
            raise ValueError("reference options must belong to changed inputs")
        return self
