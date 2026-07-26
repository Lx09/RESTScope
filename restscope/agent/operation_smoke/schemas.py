"""Public contracts for bounded Operation Smoke planning and feedback."""

from __future__ import annotations

from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)

from restscope.testing import InputGeneratorPatch, OperationExecutionReport


class AvailableReferenceOption(BaseModel):
    """One system-verified, non-empty reference pool exposed without values."""

    model_config = ConfigDict(frozen=True, extra="forbid")

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
    def validate_kind_fields(self) -> "AvailableReferenceOption":
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


class ReferenceGeneratorSelection(BaseModel):
    """Model selection of a system-provided reference option."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    input_node_id: str = Field(min_length=1, max_length=1000)
    reference_option_id: str = Field(min_length=1, max_length=100)
    inclusion_probability: float | None = Field(default=None, ge=0, le=1)


class GeneratorPatchAttribution(BaseModel):
    """Associate one concrete generator change with its planned causes."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    input_node_id: str = Field(min_length=1, max_length=1000)
    item_ids: list[str] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def require_unique_items(self) -> "GeneratorPatchAttribution":
        if len(set(self.item_ids)) != len(self.item_ids):
            raise ValueError("item_ids must be unique")
        return self


class GeneratorPatchDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    updates: list[InputGeneratorPatch] = Field(default_factory=list, max_length=100)
    reference_selections: list[ReferenceGeneratorSelection] = Field(
        default_factory=list,
        max_length=100,
    )
    attributions: list[GeneratorPatchAttribution] = Field(
        default_factory=list,
        max_length=100,
    )

    @model_validator(mode="after")
    def require_update(self) -> "GeneratorPatchDraft":
        if not self.updates and not self.reference_selections:
            raise ValueError("at least one generator update is required")
        if len(self.updates) + len(self.reference_selections) > 100:
            raise ValueError("at most 100 generator updates are allowed")
        changed_inputs = [
            item.input_node_id
            for item in [*self.updates, *self.reference_selections]
        ]
        attributed_inputs = [item.input_node_id for item in self.attributions]
        if len(set(changed_inputs)) != len(changed_inputs):
            raise ValueError("each input may be changed at most once")
        if len(set(attributed_inputs)) != len(attributed_inputs):
            raise ValueError("each changed input requires one attribution")
        if set(changed_inputs) != set(attributed_inputs):
            raise ValueError(
                "attributions must identify every changed input exactly once"
            )
        return self


class PlanItemSummary(BaseModel):
    """One in-memory failure analysis returned for audit and tracing."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    item_id: str = Field(min_length=1, max_length=20)
    failure_refs: list[str] = Field(min_length=1, max_length=100)
    cause: str = Field(min_length=1, max_length=4000)
    confidence: float = Field(ge=0, le=1)
    affected_inputs: list[str] = Field(default_factory=list, max_length=100)
    solution: str = Field(min_length=1, max_length=4000)
    evidence_refs: list[str] = Field(default_factory=list, max_length=100)
    interaction_notes: list[str] = Field(default_factory=list, max_length=100)


class PendingPlanItemSummary(BaseModel):
    """One failure hypothesis that still needs bounded evidence."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    item_id: str = Field(min_length=1, max_length=20)
    failure_refs: list[str] = Field(min_length=1, max_length=100)
    hypothesis: str = Field(min_length=1, max_length=4000)
    missing_evidence: str = Field(min_length=1, max_length=4000)
    next_probe: str = Field(min_length=1, max_length=4000)
    evidence_refs: list[str] = Field(default_factory=list, max_length=100)


class DeferredPlanItem(BaseModel):
    """A ready analysis intentionally excluded from the joint patch."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    item_id: str = Field(min_length=1, max_length=20)
    reason: str = Field(min_length=1, max_length=4000)


class PatchItemValidationSummary(BaseModel):
    """THINK assessment of one planned failure after its patch is exercised."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    item_id: str = Field(min_length=1, max_length=20)
    status: Literal["resolved", "persisting", "unknown"]
    current_failure_refs: list[str] = Field(default_factory=list, max_length=100)
    reason: str = Field(min_length=1, max_length=4000)
    confidence: float = Field(ge=0, le=1)


class PatchValidationSummary(BaseModel):
    """Accepted and rejected portions of one candidate generator patch."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    items: list[PatchItemValidationSummary] = Field(
        default_factory=list,
        max_length=100,
    )
    accepted_item_ids: list[str] = Field(default_factory=list, max_length=100)
    accepted_input_node_ids: list[str] = Field(
        default_factory=list,
        max_length=100,
    )
    rejected_input_node_ids: list[str] = Field(
        default_factory=list,
        max_length=100,
    )

    @model_validator(mode="after")
    def validate_partitions(self) -> "PatchValidationSummary":
        item_ids = [item.item_id for item in self.items]
        accepted_items = set(self.accepted_item_ids)
        resolved_items = {
            item.item_id for item in self.items if item.status == "resolved"
        }
        if len(set(item_ids)) != len(item_ids):
            raise ValueError("validation item IDs must be unique")
        if len(accepted_items) != len(self.accepted_item_ids):
            raise ValueError("accepted item IDs must be unique")
        if accepted_items != resolved_items:
            raise ValueError("accepted item IDs must equal resolved item IDs")
        accepted_inputs = set(self.accepted_input_node_ids)
        rejected_inputs = set(self.rejected_input_node_ids)
        if len(accepted_inputs) != len(self.accepted_input_node_ids):
            raise ValueError("accepted input node IDs must be unique")
        if len(rejected_inputs) != len(self.rejected_input_node_ids):
            raise ValueError("rejected input node IDs must be unique")
        if accepted_inputs.intersection(rejected_inputs):
            raise ValueError("accepted and rejected input nodes must be disjoint")
        return self


class PlanSolveDiagnosisResult(BaseModel):
    """Bounded Plan & Solve outcome consumed by the Smoke feedback loop."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: Literal["patch_ready", "no_parameter_issue", "inconclusive"]
    termination_reason: str = Field(min_length=1, max_length=200)
    patch: GeneratorPatchDraft | None = None
    selected_reference_options: list[AvailableReferenceOption] = Field(
        default_factory=list
    )
    ready_items: list[PlanItemSummary] = Field(default_factory=list)
    pending_items: list[PendingPlanItemSummary] = Field(default_factory=list)
    non_parameter_failures: list[str] = Field(default_factory=list)
    unplanned_failures: list[str] = Field(default_factory=list)
    covered_item_ids: list[str] = Field(default_factory=list)
    deferred_items: list[DeferredPlanItem] = Field(default_factory=list)
    planning_outputs: int = Field(default=0, ge=0, le=20)
    http_tool_rounds: int = Field(default=0, ge=0, le=40)
    patch_validation: PatchValidationSummary | None = None

    @model_validator(mode="after")
    def validate_patch_status(self) -> "PlanSolveDiagnosisResult":
        if self.status == "patch_ready" and self.patch is None:
            raise ValueError("patch_ready requires a patch")
        if self.status != "patch_ready" and self.patch is not None:
            raise ValueError("only patch_ready may contain a patch")
        if self.status != "patch_ready" and self.patch_validation is not None:
            raise ValueError("only patch_ready may contain patch validation")
        if self.patch is not None:
            attributed_item_ids = {
                item_id
                for attribution in self.patch.attributions
                for item_id in attribution.item_ids
            }
            if attributed_item_ids != set(self.covered_item_ids):
                raise ValueError(
                    "patch attribution must cover exactly the covered items"
                )
        return self


class OperationSmokeRequest(BaseModel):
    """One bounded smoke attempt for a persisted operation generator config."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    operation_key: str = Field(min_length=1)
    case_count: int = Field(default=10, ge=1, le=20)
    success_rate_threshold: float = Field(default=0.8, ge=0, le=1)
    max_feedback_rounds: int = Field(default=3, ge=0, le=10)
    max_planning_outputs: int = Field(default=20, ge=1, le=20)
    max_http_tool_rounds: int = Field(default=40, ge=0, le=40)
    seed: int | None = Field(default=None, ge=0)


class OperationSmokeResult(BaseModel):
    """Audit-friendly outcome produced without replaying an individual case."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: Literal["passed", "retry", "unsupported", "errored"]
    operation_key: str
    success_rate: float = Field(ge=0, le=1)
    required_success_rate: float = Field(ge=0, le=1)
    active_config_revision: int = Field(ge=1)
    batch_reports: list[OperationExecutionReport] = Field(default_factory=list)
    diagnoses: list[PlanSolveDiagnosisResult] = Field(default_factory=list)
    failure_kind: Literal[
        "threshold_exhausted",
        "no_parameter_issue",
        "diagnosis_inconclusive",
        "unsupported_operation",
        "operation_error",
    ] | None = None
    error: dict[str, str] | None = None

    @model_validator(mode="after")
    def validate_failure_kind(self) -> "OperationSmokeResult":
        allowed = {
            "passed": {None},
            "retry": {
                "threshold_exhausted",
                "no_parameter_issue",
                "diagnosis_inconclusive",
            },
            "unsupported": {"unsupported_operation"},
            "errored": {"operation_error"},
        }
        if self.failure_kind not in allowed[self.status]:
            raise ValueError(
                f"{self.status} has incompatible failure_kind "
                f"{self.failure_kind!r}"
            )
        return self
