"""Public contracts for bounded Operation Smoke planning and feedback."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)

from restscope.testing import OperationExecutionReport


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
    accepted_constraint_ids: list[str] = Field(
        default_factory=list,
        max_length=20,
    )
    rejected_constraint_ids: list[str] = Field(
        default_factory=list,
        max_length=20,
    )
    accepted_group_ids: list[str] = Field(
        default_factory=list,
        max_length=100,
    )
    rejected_group_ids: list[str] = Field(
        default_factory=list,
        max_length=100,
    )

    @model_validator(mode="after")
    def validate_partitions(self) -> "PatchValidationSummary":
        """
        Validate partitions for the run-local Operation Smoke diagnosis and candidate
        workflow.

        The class owns any required collaborators or state; arguments supply only the
        data needed for this call.
        """
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
        accepted_constraints = set(self.accepted_constraint_ids)
        rejected_constraints = set(self.rejected_constraint_ids)
        if len(accepted_constraints) != len(self.accepted_constraint_ids):
            raise ValueError("accepted constraint IDs must be unique")
        if len(rejected_constraints) != len(self.rejected_constraint_ids):
            raise ValueError("rejected constraint IDs must be unique")
        if accepted_constraints.intersection(rejected_constraints):
            raise ValueError(
                "accepted and rejected constraint IDs must be disjoint"
            )
        accepted_groups = set(self.accepted_group_ids)
        rejected_groups = set(self.rejected_group_ids)
        if len(accepted_groups) != len(self.accepted_group_ids):
            raise ValueError("accepted group IDs must be unique")
        if len(rejected_groups) != len(self.rejected_group_ids):
            raise ValueError("rejected group IDs must be unique")
        if accepted_groups.intersection(rejected_groups):
            raise ValueError(
                "accepted and rejected group IDs must be disjoint"
            )
        return self


class ParameterSolution(BaseModel):
    """One actionable parameter behavior derived from failure evidence."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    input: str = Field(min_length=1, max_length=1000)
    desired_behavior: str = Field(min_length=1, max_length=4000)
    candidate_values: list[Any] = Field(default_factory=list, max_length=100)
    candidate_range: list[float | int] | None = Field(
        default=None,
        min_length=2,
        max_length=2,
    )


class FailureHypothesis(BaseModel):
    """One testable explanation for an active failure investigation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    hypothesis_id: str = Field(min_length=1, max_length=20)
    statement: str = Field(min_length=1, max_length=4000)
    target_inputs: list[str] = Field(min_length=1, max_length=100)
    proposed_changes: list[str] = Field(min_length=1, max_length=100)
    expected_outcome: str = Field(min_length=1, max_length=4000)
    evidence_refs: list[str] = Field(default_factory=list, max_length=100)


class FailureInvestigationState(BaseModel):
    """Mutable, run-local state for exactly one active failure."""

    model_config = ConfigDict(
        extra="forbid",
        validate_assignment=True,
    )

    failure_ref: str = Field(min_length=1, max_length=20)
    root_failure_refs: list[str] = Field(min_length=1, max_length=10)
    active_hypothesis: FailureHypothesis | None = None
    inherited_observation_refs: set[str] = Field(default_factory=set)
    probe_observation_refs: set[str] = Field(default_factory=set)
    last_hypothesis_signature: str | None = None
    repeated_hypothesis_outputs: int = Field(default=0, ge=0, le=3)
    valid_outputs: int = Field(default=0, ge=0, le=20)
    consecutive_invalid_outputs: int = Field(default=0, ge=0, le=3)
    invalid_outputs: int = Field(default=0, ge=0)
    hypothesis_count: int = Field(default=0, ge=0)
    http_tool_calls: int = Field(default=0, ge=0)


class ActionableFailure(BaseModel):
    """A root-cause conclusion that may be grouped for parameter patching."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    item_id: str = Field(min_length=1, max_length=20)
    failure_ref: str = Field(min_length=1, max_length=20)
    root_failure_refs: list[str] = Field(min_length=1, max_length=10)
    evidence_origin: Literal["initial", "probe"]
    cause: str = Field(min_length=1, max_length=4000)
    solutions: list[ParameterSolution] = Field(min_length=1, max_length=100)
    affected_inputs: list[str] = Field(min_length=1, max_length=100)
    evidence_refs: list[str] = Field(min_length=1, max_length=100)
    interaction_notes: list[str] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def validate_affected_inputs(self) -> "ActionableFailure":
        """
        Validate affected inputs for the run-local Operation Smoke diagnosis and
        candidate workflow.

        The class owns any required collaborators or state; arguments supply only the
        data needed for this call.
        """
        derived = list(dict.fromkeys(item.input for item in self.solutions))
        if self.affected_inputs != derived:
            raise ValueError(
                "affected_inputs must match solution inputs in first-seen order"
            )
        if len(set(self.root_failure_refs)) != len(self.root_failure_refs):
            raise ValueError("root_failure_refs must be unique")
        return self


class FailureInvestigationSummary(BaseModel):
    """Bounded audit summary for one in-memory failure investigation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    failure_ref: str = Field(min_length=1, max_length=20)
    root_failure_refs: list[str] = Field(min_length=1, max_length=10)
    status: Literal["ready", "confirmed", "deferred"]
    valid_outputs: int = Field(default=0, ge=0, le=20)
    invalid_outputs: int = Field(default=0, ge=0)
    hypothesis_count: int = Field(default=0, ge=0)
    http_tool_calls: int = Field(default=0, ge=0)
    reason: str | None = Field(default=None, max_length=4000)


class DeferredFailure(BaseModel):
    """One failure that could not produce an actionable parameter solution."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    failure_ref: str = Field(min_length=1, max_length=20)
    root_failure_refs: list[str] = Field(min_length=1, max_length=10)
    reason: str = Field(min_length=1, max_length=4000)


class PatchGroupRunSummary(BaseModel):
    """Bounded audit result for one isolated Parameter Patch Agent."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    group_id: str = Field(min_length=1, max_length=20)
    item_ids: list[str] = Field(min_length=1, max_length=100)
    root_failure_refs: list[str] = Field(min_length=1, max_length=10)
    status: Literal["validated", "failed"]
    attempts: int = Field(ge=1, le=20)
    failure_reason: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def validate_failure_reason(self) -> "PatchGroupRunSummary":
        """
        Validate failure reason for the run-local Operation Smoke diagnosis and
        candidate workflow.

        The class owns any required collaborators or state; arguments supply only the
        data needed for this call.
        """
        if self.status == "failed" and self.failure_reason is None:
            raise ValueError("failed Patch Group runs require a reason")
        if self.status == "validated" and self.failure_reason is not None:
            raise ValueError(
                "validated Patch Group runs cannot have a failure reason"
            )
        return self


class PlanSolveDiagnosisResult(BaseModel):
    """Root-cause investigation outcome consumed by the Smoke feedback loop."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: Literal["actionable", "no_parameter_issue", "inconclusive"]
    termination_reason: str = Field(min_length=1, max_length=200)
    investigations: list[FailureInvestigationSummary] = Field(
        default_factory=list,
        max_length=10,
    )
    actionable_failures: list[ActionableFailure] = Field(
        default_factory=list,
        max_length=10,
    )
    deferred_failures: list[DeferredFailure] = Field(
        default_factory=list,
        max_length=10,
    )
    truncated_failure_refs: list[str] = Field(
        default_factory=list,
        max_length=100,
    )
    valid_outputs: int = Field(default=0, ge=0, le=200)
    invalid_outputs: int = Field(default=0, ge=0)
    http_tool_calls: int = Field(default=0, ge=0)
    patch_group_runs: list[PatchGroupRunSummary] = Field(
        default_factory=list,
        max_length=100,
    )
    patch_validation: PatchValidationSummary | None = None

    @model_validator(mode="after")
    def validate_status(self) -> "PlanSolveDiagnosisResult":
        """
        Validate status for the run-local Operation Smoke diagnosis and candidate
        workflow.

        The class owns any required collaborators or state; arguments supply only the
        data needed for this call.
        """
        if self.status == "actionable" and not self.actionable_failures:
            raise ValueError("actionable requires at least one actionable failure")
        if self.status != "actionable" and self.actionable_failures:
            raise ValueError(
                "only actionable diagnoses may contain actionable failures"
            )
        return self


class OperationSmokeRequest(BaseModel):
    """One bounded smoke attempt for a persisted operation generator config."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    operation_key: str = Field(min_length=1)
    case_count: int = Field(default=10, ge=1, le=20)
    success_rate_threshold: float = Field(default=0.8, ge=0, le=1)
    max_feedback_rounds: int = Field(default=3, ge=0, le=10)
    max_diagnosis_outputs_per_failure: int = Field(default=20, ge=1, le=20)
    max_patch_attempts: int = Field(default=20, ge=2, le=20)
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
        """
        Validate failure kind for the run-local Operation Smoke diagnosis and candidate
        workflow.

        The class owns any required collaborators or state; arguments supply only the
        data needed for this call.
        """
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
