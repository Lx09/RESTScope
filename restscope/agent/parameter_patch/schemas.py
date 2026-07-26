"""Public contracts owned by the Parameter Patch Agent."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from restscope.testing import ConstraintSet, InputGeneratorPatch
from restscope.testing.models import GeneratorStrategy


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class AvailableReferenceOption(_Model):
    option_id: str = Field(min_length=1, max_length=100)
    input_node_id: str = Field(min_length=1, max_length=1000)
    kind: Literal["resource_identifier", "response_value"]
    canonical_resource: str | None = Field(default=None, max_length=200)
    value_name: str | None = Field(default=None, max_length=200)
    compatible_scalar_type: str | None = Field(default=None, max_length=50)
    value_count: int = Field(ge=1)
    producer_operation_keys: list[str] = Field(
        default_factory=list,
        max_length=100,
    )
    producer_status_code: str | None = Field(default=None, max_length=20)
    producer_media_type: str | None = Field(default=None, max_length=200)
    source_field: str | None = Field(default=None, max_length=1000)
    source_selector: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def validate_source(self) -> "AvailableReferenceOption":
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
            raise ValueError(
                "response_value requires one complete producer source"
            )
        return self


class PatchGroupTask(_Model):
    group_id: str = Field(min_length=1, max_length=20)
    item_ids: list[str] = Field(min_length=1, max_length=100)
    root_failure_refs: list[str] = Field(min_length=1, max_length=10)
    inputs: list[str] = Field(min_length=1, max_length=100)
    objective: str = Field(min_length=1, max_length=4000)
    requirements: list[str] = Field(min_length=1, max_length=100)
    candidate_hints: list[Any] = Field(default_factory=list, max_length=100)
    interaction_notes: list[str] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def validate_unique_lists(self) -> "PatchGroupTask":
        for name in ("item_ids", "root_failure_refs", "inputs"):
            values = getattr(self, name)
            if len(set(values)) != len(values):
                raise ValueError(f"{name} must be unique")
        return self


class SemanticGeneratorChange(_Model):
    input: str = Field(min_length=1, max_length=1000)
    inclusion_probability: float | None = Field(default=None, ge=0, le=1)
    strategy: GeneratorStrategy | None = None
    reference: str | None = Field(default=None, min_length=1, max_length=20)

    @model_validator(mode="after")
    def require_change(self) -> "SemanticGeneratorChange":
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
    expression: dict[str, Any]


class ParameterPatchProposal(_Model):
    changes: list[SemanticGeneratorChange] = Field(
        default_factory=list,
        max_length=100,
    )
    constraints: list[SemanticConstraintChange] = Field(
        default_factory=list,
        max_length=20,
    )

    @model_validator(mode="after")
    def require_patch(self) -> "ParameterPatchProposal":
        if not self.changes and not self.constraints:
            raise ValueError("a patch must contain a generator or constraint")
        return self


class ParameterPatchDecision(_Model):
    action: Literal["propose", "accept"]
    patch: ParameterPatchProposal | None = None

    @model_validator(mode="after")
    def validate_action(self) -> "ParameterPatchDecision":
        if self.action == "propose" and self.patch is None:
            raise ValueError("propose requires a complete patch")
        if self.action == "accept" and self.patch is not None:
            raise ValueError("accept must not include a patch")
        return self


class GeneratorPatchAttribution(_Model):
    input_node_id: str = Field(min_length=1, max_length=1000)
    group_ids: list[str] = Field(min_length=1, max_length=100)
    item_ids: list[str] = Field(min_length=1, max_length=100)
    root_failure_refs: list[str] = Field(min_length=1, max_length=10)


class CompiledConstraintPatch(_Model):
    constraint_id: str = Field(min_length=1, max_length=100)
    group_ids: list[str] = Field(min_length=1, max_length=100)
    item_ids: list[str] = Field(min_length=1, max_length=100)
    root_failure_refs: list[str] = Field(min_length=1, max_length=10)
    kind: str = Field(min_length=1, max_length=100)
    constraint: ConstraintSet


class GeneratorPatchDraft(_Model):
    updates: list[InputGeneratorPatch] = Field(
        default_factory=list,
        max_length=100,
    )
    attributions: list[GeneratorPatchAttribution] = Field(
        default_factory=list,
        max_length=100,
    )
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
        if not self.updates and not self.constraints:
            raise ValueError("a compiled patch cannot be empty")
        changed = [item.input_node_id for item in self.updates]
        attributed = [item.input_node_id for item in self.attributions]
        if len(set(changed)) != len(changed):
            raise ValueError("each input may be changed at most once")
        if changed != attributed:
            raise ValueError("attributions must follow all updates in order")
        selected_inputs = [
            item.input_node_id for item in self.selected_reference_options
        ]
        if len(set(selected_inputs)) != len(selected_inputs):
            raise ValueError(
                "each input may select at most one reference option"
            )
        if not set(selected_inputs).issubset(set(changed)):
            raise ValueError(
                "selected reference options must belong to changed inputs"
            )
        return self


class ValidatedPatchGroup(_Model):
    status: Literal["validated"] = "validated"
    group_id: str = Field(min_length=1, max_length=20)
    item_ids: list[str] = Field(min_length=1, max_length=100)
    root_failure_refs: list[str] = Field(min_length=1, max_length=10)
    patch: GeneratorPatchDraft
    samples: list[dict[str, Any]] = Field(min_length=10, max_length=10)
    attempts: int = Field(ge=2, le=20)


class PatchGroupFailure(_Model):
    status: Literal["failed"] = "failed"
    group_id: str = Field(min_length=1, max_length=20)
    item_ids: list[str] = Field(min_length=1, max_length=100)
    root_failure_refs: list[str] = Field(min_length=1, max_length=10)
    reason: Literal["attempt_limit"]
    attempts: int = Field(ge=1, le=20)
    errors: list[str] = Field(default_factory=list, max_length=20)
