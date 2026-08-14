"""Define immutable data exchanged inside the long-task Orchestration loop.

The Orchestrator proposes revisions and one task at a time, the Task Executor
returns criterion-level results, and the Ledger records accepted transitions.
These models contain no runtime behavior and are never persisted.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    RootModel,
    field_validator,
    model_validator,
)

from restscope.agent.contracts import AgentFinding, AgentResultStatus


class _FrozenModel(BaseModel):
    """Make every orchestration value closed and immutable after validation."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class GoalCriterion(_FrozenModel):
    """Name one product-level condition the Orchestrator must assess."""

    criterion_id: str = Field(pattern=r"^goal_[1-9][0-9]*$")
    description: str = Field(min_length=1, max_length=1_000)


class GoalContract(_FrozenModel):
    """Hold the fixed RESTScope mission plus the user's optional run focus."""

    mission: str = Field(min_length=1, max_length=4_000)
    focus: str | None = Field(default=None, max_length=4_000)
    success_criteria: tuple[GoalCriterion, ...] = Field(min_length=1, max_length=20)


class MilestoneProposal(_FrozenModel):
    """Describe future work without assigning Ledger-owned identities."""

    title: str = Field(min_length=1, max_length=200)
    purpose: str = Field(min_length=1, max_length=2_000)
    success_criteria: tuple[str, ...] = Field(min_length=1, max_length=20)
    supersedes_milestone_id: str | None = Field(
        default=None, pattern=r"^milestone_[1-9][0-9]*$"
    )


class ReplanDecision(_FrozenModel):
    """Replace future work while preserving the Goal and prior evidence."""

    kind: Literal["replan"]
    expected_plan_revision: int = Field(ge=0)
    reason: str = Field(min_length=1, max_length=2_000)
    completed_milestone_ids: tuple[str, ...] = Field(default=(), max_length=20)
    milestones: tuple[MilestoneProposal, ...] = Field(min_length=1, max_length=20)

    @field_validator("completed_milestone_ids")
    @classmethod
    def require_unique_completed_milestone_ids(
        cls, values: tuple[str, ...]
    ) -> tuple[str, ...]:
        """Reject ambiguous duplicate completion transitions in one revision."""
        if len(values) != len(set(values)):
            raise ValueError("Completed Milestone IDs must be unique")
        return values


class TaskCriterion(_FrozenModel):
    """Give a Task Executor one independently reportable completion condition."""

    criterion_id: str = Field(pattern=r"^criterion_[1-9][0-9]*$")
    description: str = Field(min_length=1, max_length=2_000)


class TaskProposal(_FrozenModel):
    """Describe the single bounded task the Orchestrator wants dispatched."""

    milestone_id: str = Field(pattern=r"^milestone_[1-9][0-9]*$")
    objective: str = Field(min_length=1, max_length=4_000)
    purpose: str = Field(min_length=1, max_length=2_000)
    success_criteria: tuple[TaskCriterion, ...] = Field(min_length=1, max_length=30)
    related_attempt_ids: tuple[str, ...] = Field(default=(), max_length=20)
    retry_reason: str | None = Field(default=None, min_length=1, max_length=2_000)

    @field_validator("success_criteria")
    @classmethod
    def require_unique_criterion_ids(
        cls, values: tuple[TaskCriterion, ...]
    ) -> tuple[TaskCriterion, ...]:
        """Prevent an ambiguous execution result contract before dispatch."""
        identities = tuple(value.criterion_id for value in values)
        if len(identities) != len(set(identities)):
            raise ValueError("Task criterion IDs must be unique")
        return values

    @field_validator("related_attempt_ids")
    @classmethod
    def require_unique_attempt_ids(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        """Keep selected history unambiguous and within stable Attempt syntax."""
        if len(values) != len(set(values)):
            raise ValueError("Related Attempt IDs must be unique")
        if any(
            not value.removeprefix("attempt_").isdigit()
            or not value.startswith("attempt_")
            for value in values
        ):
            raise ValueError("Related Attempt IDs must use attempt_N syntax")
        return values


class DispatchTaskDecision(_FrozenModel):
    """Request one Task Executor run against the current plan revision."""

    kind: Literal["dispatch_task"]
    expected_plan_revision: int = Field(ge=1)
    task: TaskProposal


class GoalCriterionVerdict(_FrozenModel):
    """Explain whether final evidence satisfies one Goal criterion."""

    criterion_id: str = Field(pattern=r"^goal_[1-9][0-9]*$")
    status: Literal["met", "not_met", "unknown"]
    explanation: str = Field(min_length=1, max_length=2_000)


class CompleteDecision(_FrozenModel):
    """Finish the run only after assessing every fixed Goal criterion."""

    kind: Literal["complete"]
    expected_plan_revision: int = Field(ge=1)
    goal_criteria: tuple[GoalCriterionVerdict, ...] = Field(min_length=1, max_length=20)
    summary: str = Field(min_length=1, max_length=16_000)
    unresolved: tuple[str, ...] = Field(default=(), max_length=100)


class OrchestratorDecision(
    RootModel[
        Annotated[
            ReplanDecision | DispatchTaskDecision | CompleteDecision,
            Field(discriminator="kind"),
        ]
    ]
):
    """Parse exactly one mutually exclusive Orchestrator decision."""


class CriterionVerdict(_FrozenModel):
    """Report one Task criterion exactly once with optional evidence labels."""

    criterion_id: str = Field(pattern=r"^criterion_[1-9][0-9]*$")
    status: Literal["met", "not_met", "unknown"]
    explanation: str = Field(min_length=1, max_length=4_000)
    evidence_refs: tuple[str, ...] = Field(default=(), max_length=50)


class TaskExecutionResult(_FrozenModel):
    """Return only what happened in one executed Task, never the next plan."""

    task_id: str = Field(pattern=r"^task_[1-9][0-9]*$")
    outcome: Literal["completed", "partial", "blocked"]
    criteria: tuple[CriterionVerdict, ...] = Field(min_length=1, max_length=30)
    findings: tuple[AgentFinding, ...] = Field(default=(), max_length=100)
    unresolved_issues: tuple[str, ...] = Field(default=(), max_length=100)
    target_state_changes: tuple[str, ...] = Field(default=(), max_length=100)


class PlanRevisionRecord(_FrozenModel):
    """Remember why one accepted Replan replaced the future work."""

    plan_revision: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=2_000)
    completed_milestone_ids: tuple[str, ...]
    superseded_milestone_ids: tuple[str, ...]
    created_milestone_ids: tuple[str, ...] = Field(min_length=1)


class MilestoneRecord(_FrozenModel):
    """Store one revision-owned milestone and its current lifecycle state."""

    milestone_id: str
    plan_revision: int
    title: str
    purpose: str
    success_criteria: tuple[str, ...]
    status: Literal["pending", "completed", "superseded"] = "pending"
    supersedes_milestone_id: str | None = None


class TaskRecord(_FrozenModel):
    """Store the exact Task Executor assignment accepted by the Ledger."""

    task_id: str
    milestone_id: str
    plan_revision: int
    objective: str
    purpose: str
    success_criteria: tuple[TaskCriterion, ...]
    related_attempt_ids: tuple[str, ...]
    retry_reason: str | None = None
    status: Literal["running", "completed", "partial", "blocked", "failed"]


class AttemptRecord(_FrozenModel):
    """Preserve one immutable Task execution outcome or lifecycle failure."""

    attempt_id: str
    task_id: str
    plan_revision: int
    outcome: Literal["completed", "partial", "blocked", "failed"]
    result: TaskExecutionResult | None = None
    failure_code: str | None = None
    failure_message: str | None = None

    @model_validator(mode="after")
    def require_result_or_failure_payload(self) -> AttemptRecord:
        """Keep Task results and lifecycle failures mutually exclusive."""
        if self.outcome == "failed":
            if self.result is not None or self.failure_code is None or self.failure_message is None:
                raise ValueError("Failed Attempt requires failure details only")
        elif self.result is None or self.failure_code is not None or self.failure_message is not None:
            raise ValueError("Task execution Attempt requires one structured result only")
        return self


class TaskLedgerSnapshot(_FrozenModel):
    """Expose a read-only point-in-time view of the in-memory Ledger."""

    plan_revision: int
    run_status: Literal["planning", "running", "completed"]
    plan_revisions: tuple[PlanRevisionRecord, ...]
    milestones: tuple[MilestoneRecord, ...]
    tasks: tuple[TaskRecord, ...]
    attempts: tuple[AttemptRecord, ...]


class OrchestrationResult(_FrozenModel):
    """Return the completed summary with the immutable Goal and Ledger view."""

    summary: str
    unresolved: tuple[str, ...]
    goal: GoalContract
    ledger: TaskLedgerSnapshot


class OrchestrationSessionRecord(_FrozenModel):
    """Link one fresh System Agent root to accepted orchestration state.

    Profile names describe capabilities but are not identities. ``session_id``
    is therefore the only key a browser may use to open a conversation, while
    the role-local sequence gives people a compact stable label.
    """

    session_id: str = Field(min_length=1, max_length=160)
    profile_name: str = Field(min_length=1, max_length=120)
    role: Literal["orchestrator", "task_executor"]
    sequence: int = Field(ge=1)
    status: AgentResultStatus
    decision_kind: Literal["replan", "dispatch_task", "complete"] | None = None
    task_id: str | None = Field(default=None, pattern=r"^task_[1-9][0-9]*$")
    attempt_id: str | None = Field(default=None, pattern=r"^attempt_[1-9][0-9]*$")


class OrchestrationObservation(_FrozenModel):
    """Expose one complete read-only Goal, Ledger, and root-session projection."""

    revision: int = Field(ge=1)
    goal: GoalContract
    ledger: TaskLedgerSnapshot
    sessions: tuple[OrchestrationSessionRecord, ...] = Field(default=(), max_length=10_000)
