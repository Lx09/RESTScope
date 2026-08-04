"""Public contracts owned by the Parameter Patch Module.

Failure Solve supplies one root cause and Patch requirement. Parameter Patch
translates that requirement into executable Generator and Constraint objects,
then returns the complete validated Patch or a bounded failure record. No
failure aliases, Patch Groups, or cross-failure ownership appear here.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, model_validator

from restscope.testing import ConstraintSet, InputGeneratorPatch
from restscope.testing.models import (
    ArrayGenerator,
    BooleanGenerator,
    ChoiceGenerator,
    ConstantGenerator,
    FormatGenerator,
    IntegerRangeGenerator,
    NumberRangeGenerator,
    RandomStringGenerator,
    RegexGenerator,
    VariantGenerator,
)


class _Model(BaseModel):
    """Use frozen strict DTOs for model and Agent handoffs."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class AvailableReferenceOption(_Model):
    """One populated reference source the model may select by temporary alias."""

    option_id: str = Field(min_length=1, max_length=100)
    input_node_id: str = Field(min_length=1, max_length=1000)
    kind: Literal["resource_identifier", "response_value"]
    canonical_resource: str | None = Field(default=None, max_length=200)
    value_name: str | None = Field(default=None, max_length=200)
    compatible_scalar_type: str | None = Field(default=None, max_length=50)
    value_count: int = Field(ge=1)
    producer_operation_keys: list[str] = Field(default_factory=list, max_length=100)
    producer_status_code: str | None = Field(default=None, max_length=20)
    producer_media_type: str | None = Field(default=None, max_length=200)
    source_field: str | None = Field(default=None, max_length=1000)
    source_selector: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def validate_source(self) -> "AvailableReferenceOption":
        """Require the source fields appropriate to the selected reference kind."""
        if self.kind == "resource_identifier":
            if not self.canonical_resource or self.value_name is not None:
                raise ValueError(
                    "resource_identifier requires canonical_resource only"
                )
        elif (
            not self.value_name
            or self.canonical_resource is not None
            or len(self.producer_operation_keys) != 1
            or not self.producer_status_code
            or not self.producer_media_type
            or not self.source_field
            or not self.source_selector
        ):
            raise ValueError("response_value requires one complete producer source")
        return self


class ParameterPatchTask(_Model):
    """One evidence-backed value requirement that Parameter Patch must satisfy.

    ``root_cause`` diagnoses the current generated values. The separate
    ``value_requirements`` field states the target value domain, while each
    acceptance criterion gives the Reviewer one independently checkable value
    predicate. HTTP outcomes deliberately belong to Solve evidence, not this
    Patch-construction handoff.
    """

    todo_id: str = Field(min_length=1, max_length=100)
    failure: str = Field(min_length=1)
    root_cause: str = Field(min_length=1)
    affected_inputs: list[str] = Field(min_length=1, max_length=100)
    value_requirements: str = Field(min_length=1)
    acceptance_criteria: list[str] = Field(min_length=1, max_length=20)
    prior_attempts: list[dict[str, Any]] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_inputs(self) -> "ParameterPatchTask":
        """Prevent ambiguous duplicate input ownership inside one requirement."""
        if len(self.affected_inputs) != len(set(self.affected_inputs)):
            raise ValueError("affected_inputs must be unique")
        if len(self.acceptance_criteria) != len(set(self.acceptance_criteria)):
            raise ValueError("acceptance_criteria must be unique")
        return self


# Reuse the Testing Module's field-level validation for strategies whose model
# and runtime meanings are identical. The narrower union is the Patch model's
# authority: system-managed and observed-value strategies are deliberately not
# choices here.
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
    | VariantGenerator,
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
    value: Any


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
    """One model-facing Generator change using a semantic input path."""

    input: str = Field(min_length=1, max_length=1000)
    inclusion_probability: float | None = Field(default=None, ge=0, le=1)
    strategy: SemanticGeneratorStrategy | None = None
    reference: str | None = Field(default=None, min_length=1, max_length=20)

    @model_validator(mode="after")
    def require_change(self) -> "SemanticGeneratorChange":
        """Require a concrete change and forbid direct-plus-reference strategies."""
        if self.strategy is not None and self.reference is not None:
            raise ValueError("strategy and reference are mutually exclusive")
        if (
            self.inclusion_probability is None
            and self.strategy is None
            and self.reference is None
        ):
            raise ValueError("a generator change must change at least one field")
        return self


class SemanticConstraintChange(_Model):
    """One recursive semantic Constraint expression."""

    expression: SemanticBooleanExpression


class ParameterPatchProposal(_Model):
    """One complete replacement Patch proposed by the model."""

    changes: list[SemanticGeneratorChange] = Field(default_factory=list, max_length=100)
    constraints: list[SemanticConstraintChange] = Field(
        default_factory=list,
        max_length=20,
    )

    @model_validator(mode="after")
    def require_patch(self) -> "ParameterPatchProposal":
        """Reject an empty proposal before compilation."""
        if not self.changes and not self.constraints:
            raise ValueError("a patch must contain a generator or constraint")
        return self


class ParameterPatchSubmission(_Model):
    """Carry the Patch Agent's one allowed final decision: a full proposal."""

    action: Literal["propose"]
    patch: ParameterPatchProposal


class CompiledConstraintPatch(_Model):
    """One stable, executable Constraint owned by the current requirement."""

    constraint_id: str = Field(min_length=1, max_length=100)
    kind: str = Field(min_length=1, max_length=100)
    constraint: ConstraintSet


class GeneratorPatchDraft(_Model):
    """Complete executable Generator and Constraint candidate."""

    updates: list[InputGeneratorPatch] = Field(default_factory=list, max_length=100)
    constraints: list[CompiledConstraintPatch] = Field(
        default_factory=list,
        max_length=20,
    )
    selected_reference_options: list[AvailableReferenceOption] = Field(
        default_factory=list,
        max_length=100,
    )

    @model_validator(mode="after")
    def validate_patch(self) -> "GeneratorPatchDraft":
        """Reject empty, duplicate, or mismatched reference-backed candidates."""
        if not self.updates and not self.constraints:
            raise ValueError("a compiled patch cannot be empty")
        changed = [item.input_node_id for item in self.updates]
        if len(changed) != len(set(changed)):
            raise ValueError("each input may be changed at most once")
        selected = [
            item.input_node_id for item in self.selected_reference_options
        ]
        if len(selected) != len(set(selected)):
            raise ValueError("each input may select at most one reference option")
        if not set(selected).issubset(set(changed)):
            raise ValueError("reference options must belong to changed inputs")
        return self


class ValidatedParameterPatch(_Model):
    """Patch accepted after executable checks and independent semantic review."""

    status: Literal["validated"] = "validated"
    todo_id: str
    patch: GeneratorPatchDraft
    samples: list[dict[str, Any]] = Field(min_length=1, max_length=20)
    outputs_used: int = Field(ge=2, le=20)
    attempt_history: list[dict[str, Any]] = Field(default_factory=list)


class ParameterPatchFailure(_Model):
    """Bounded Patch failure returned to the same Failure Solve session."""

    status: Literal["failed"] = "failed"
    todo_id: str
    reason: Literal["output_budget_exhausted", "repeated_invalid_output"]
    outputs_used: int = Field(ge=1, le=20)
    errors: list[str] = Field(default_factory=list, max_length=20)
    attempt_history: list[dict[str, Any]] = Field(default_factory=list)
