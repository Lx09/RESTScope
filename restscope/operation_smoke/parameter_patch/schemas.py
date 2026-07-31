"""Public contracts owned by the Parameter Patch Agent.

Failure Solve supplies one root cause and Patch requirement. Parameter Patch
translates that requirement into executable Generator and Constraint objects,
then returns the complete validated Patch or a bounded failure record. No
failure aliases, Patch Groups, or cross-failure ownership appear here.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from restscope.testing import ConstraintSet, InputGeneratorPatch
from restscope.testing.models import GeneratorStrategy


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
    """One Solve-owned requirement that Parameter Patch must satisfy."""

    todo_id: str = Field(min_length=1, max_length=100)
    failure: str = Field(min_length=1)
    root_cause: str = Field(min_length=1)
    affected_inputs: list[str] = Field(min_length=1, max_length=100)
    desired_behavior: str = Field(min_length=1)
    acceptance_criteria: str = Field(min_length=1)
    prior_attempts: list[dict[str, Any]] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_inputs(self) -> "ParameterPatchTask":
        """Prevent ambiguous duplicate input ownership inside one requirement."""
        if len(self.affected_inputs) != len(set(self.affected_inputs)):
            raise ValueError("affected_inputs must be unique")
        return self


class SemanticGeneratorChange(_Model):
    """One model-facing Generator change using a semantic input path."""

    input: str = Field(min_length=1, max_length=1000)
    inclusion_probability: float | None = Field(default=None, ge=0, le=1)
    strategy: GeneratorStrategy | None = None
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

    expression: dict[str, Any]


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


class ParameterPatchDecision(_Model):
    """Propose a complete Patch or accept the latest compiled sample review."""

    action: Literal["propose", "accept"]
    patch: ParameterPatchProposal | None = None

    @model_validator(mode="after")
    def validate_action(self) -> "ParameterPatchDecision":
        """Keep proposal and acceptance shapes mutually exclusive."""
        if self.action == "propose" and self.patch is None:
            raise ValueError("propose requires a complete patch")
        if self.action == "accept" and self.patch is not None:
            raise ValueError("accept must not include a patch")
        return self


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
    """Patch accepted after executable checks and dynamic local sample review."""

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
