"""Own every legal mutation of the in-memory long-task Ledger.

The runtime asks this class to apply a plan revision, dispatch one task, append
one immutable Attempt, or complete the run. Callers receive frozen records and
cannot modify Ledger collections directly.
"""

from __future__ import annotations

from typing import Literal

from .models import (
    AttemptRecord,
    CompleteDecision,
    GoalContract,
    MilestoneRecord,
    PlanRevisionRecord,
    ReplanDecision,
    TaskExecutionResult,
    TaskLedgerSnapshot,
    TaskProposal,
    TaskRecord,
)


class TaskLedger:
    """Validate and record the complete App-lifetime orchestration state."""

    def __init__(self, goal: GoalContract) -> None:
        """Create an empty Ledger anchored to an immutable Goal contract."""
        self._goal = goal
        self._plan_revision = 0
        self._run_status: Literal["planning", "running", "completed"] = "planning"
        self._plan_revisions: list[PlanRevisionRecord] = []
        self._milestones: list[MilestoneRecord] = []
        self._tasks: list[TaskRecord] = []
        self._attempts: list[AttemptRecord] = []
        self._active_task_id: str | None = None

    @property
    def goal(self) -> GoalContract:
        """Return the immutable Goal that no plan transition may replace."""
        return self._goal

    @property
    def plan_revision(self) -> int:
        """Return the revision the next decision must explicitly acknowledge."""
        return self._plan_revision

    def apply_replan(self, decision: ReplanDecision) -> None:
        """Supersede unfinished work and append a materially new future plan.

        The method rejects stale revisions, active Task execution, unknown reopen
        targets, and no-op replans. Historical Tasks and Attempts remain intact.
        """
        self._require_current_revision(decision.expected_plan_revision)
        if self._active_task_id is not None:
            raise ValueError("Cannot replan while a Task Executor is active")

        proposed_shape = tuple(
            (
                item.title,
                item.purpose,
                item.success_criteria,
                item.supersedes_milestone_id,
            )
            for item in decision.milestones
        )
        current_shape = tuple(
            (
                item.title,
                item.purpose,
                item.success_criteria,
                item.supersedes_milestone_id,
            )
            for item in self._milestones
            if item.status == "pending"
        )
        if proposed_shape == current_shape and not decision.completed_milestone_ids:
            raise ValueError("Replan must materially change future work")

        known_milestones = {item.milestone_id: item for item in self._milestones}
        current_pending_ids = {
            item.milestone_id
            for item in self._milestones
            if item.status == "pending" and item.plan_revision == self._plan_revision
        }
        completed_ids = set(decision.completed_milestone_ids)
        if len(decision.completed_milestone_ids) != len(completed_ids):
            raise ValueError("Completed Milestone IDs must be unique")
        if not completed_ids.issubset(current_pending_ids):
            raise ValueError("Only current pending Milestones may be completed")
        for proposal in decision.milestones:
            reopened = proposal.supersedes_milestone_id
            if reopened is not None and reopened not in known_milestones:
                raise ValueError(f"Unknown superseded milestone: {reopened}")

        next_revision = self._plan_revision + 1
        superseded_ids = tuple(
            item.milestone_id
            for item in self._milestones
            if item.status == "pending" and item.milestone_id not in completed_ids
        )
        updated_milestones = [
            item.model_copy(
                update={
                    "status": (
                        "completed"
                        if item.milestone_id in completed_ids
                        else "superseded"
                    )
                }
            )
            if item.status == "pending"
            else item
            for item in self._milestones
        ]
        new_milestones = [
            MilestoneRecord(
                milestone_id=f"milestone_{len(self._milestones) + index}",
                plan_revision=next_revision,
                title=proposal.title,
                purpose=proposal.purpose,
                success_criteria=proposal.success_criteria,
                supersedes_milestone_id=proposal.supersedes_milestone_id,
            )
            for index, proposal in enumerate(decision.milestones, start=1)
        ]
        revision = PlanRevisionRecord(
            plan_revision=next_revision,
            reason=decision.reason,
            completed_milestone_ids=decision.completed_milestone_ids,
            superseded_milestone_ids=superseded_ids,
            created_milestone_ids=tuple(
                item.milestone_id for item in new_milestones
            ),
        )

        # Build every frozen replacement before publishing any state. A model
        # validation failure therefore cannot leave a partially applied plan.
        self._milestones = [*updated_milestones, *new_milestones]
        self._plan_revisions.append(revision)
        self._plan_revision = next_revision
        self._run_status = "running"

    def dispatch(self, proposal: TaskProposal, *, expected_revision: int) -> TaskRecord:
        """Create one running Task after validating its plan and Milestone."""
        self._require_current_revision(expected_revision)
        if self._active_task_id is not None:
            raise ValueError("Only one Task Executor may be active")
        milestone = self._find_milestone(proposal.milestone_id)
        if milestone.status != "pending" or milestone.plan_revision != self._plan_revision:
            raise ValueError("Task must target a pending Milestone in the current plan")
        known_attempt_ids = {item.attempt_id for item in self._attempts}
        if not set(proposal.related_attempt_ids).issubset(known_attempt_ids):
            raise ValueError("Task references an unknown Attempt")
        self._reject_unchanged_failed_retry(proposal)

        task = TaskRecord(
            task_id=f"task_{len(self._tasks) + 1}",
            milestone_id=proposal.milestone_id,
            plan_revision=self._plan_revision,
            objective=proposal.objective,
            purpose=proposal.purpose,
            success_criteria=proposal.success_criteria,
            related_attempt_ids=proposal.related_attempt_ids,
            retry_reason=proposal.retry_reason,
            status="running",
        )
        self._tasks.append(task)
        self._active_task_id = task.task_id
        return task

    def append_attempt(self, result: TaskExecutionResult) -> AttemptRecord:
        """Validate one Task execution result before recording a state change."""
        task = self._require_active_task(result.task_id)
        expected_ids = tuple(item.criterion_id for item in task.success_criteria)
        actual_ids = tuple(item.criterion_id for item in result.criteria)
        if len(actual_ids) != len(set(actual_ids)) or set(actual_ids) != set(expected_ids):
            raise ValueError("Task Executor must report every Task criterion exactly once")
        if result.outcome == "completed" and any(
            item.status != "met" for item in result.criteria
        ):
            raise ValueError("A completed Task execution requires every criterion to be met")

        attempt = AttemptRecord(
            attempt_id=f"attempt_{len(self._attempts) + 1}",
            task_id=task.task_id,
            plan_revision=task.plan_revision,
            outcome=result.outcome,
            result=result,
        )
        self._attempts.append(attempt)
        self._replace_task(task, status=result.outcome)
        self._active_task_id = None
        return attempt

    def append_failure(
        self,
        task_id: str,
        *,
        failure_code: str,
        failure_message: str,
    ) -> AttemptRecord:
        """Record a terminal Task Executor failure before replanning.

        Args:
            task_id: Identity of the currently running Task.
            failure_code: Stable machine-readable Harness or Provider failure.
            failure_message: Bounded explanation safe for later model context.

        Returns:
            The newly appended immutable Attempt.
        """
        task = self._require_active_task(task_id)
        if not failure_code.strip() or len(failure_code) > 120:
            raise ValueError("Failure code must be 1-120 characters")
        if not failure_message.strip() or len(failure_message) > 2_000:
            raise ValueError("Failure message must be 1-2000 characters")
        attempt = AttemptRecord(
            attempt_id=f"attempt_{len(self._attempts) + 1}",
            task_id=task.task_id,
            plan_revision=task.plan_revision,
            outcome="failed",
            failure_code=failure_code,
            failure_message=failure_message,
        )
        self._attempts.append(attempt)
        self._replace_task(task, status="failed")
        self._active_task_id = None
        return attempt

    def complete(self, decision: CompleteDecision) -> TaskLedgerSnapshot:
        """Finish only a current, planned run with every Goal criterion assessed."""
        self._require_current_revision(decision.expected_plan_revision)
        if self._plan_revision == 0:
            raise ValueError("The Orchestrator must create a plan before completion")
        if self._active_task_id is not None:
            raise ValueError("Cannot complete while a Task Executor is active")
        expected_ids = {item.criterion_id for item in self._goal.success_criteria}
        actual_ids = tuple(item.criterion_id for item in decision.goal_criteria)
        if len(actual_ids) != len(set(actual_ids)) or set(actual_ids) != expected_ids:
            raise ValueError("Completion must assess every Goal criterion exactly once")
        self._run_status = "completed"
        return self.snapshot()

    def snapshot(self) -> TaskLedgerSnapshot:
        """Return frozen tuples so prompt projection cannot mutate the Ledger."""
        return TaskLedgerSnapshot(
            plan_revision=self._plan_revision,
            run_status=self._run_status,
            plan_revisions=tuple(self._plan_revisions),
            milestones=tuple(self._milestones),
            tasks=tuple(self._tasks),
            attempts=tuple(self._attempts),
        )

    def related_attempts(self, task: TaskRecord) -> tuple[AttemptRecord, ...]:
        """Return only the bounded history explicitly selected for this Task."""
        requested = set(task.related_attempt_ids)
        return tuple(item for item in self._attempts if item.attempt_id in requested)

    def _require_current_revision(self, expected_revision: int) -> None:
        """Reject a stale Agent decision before it changes Ledger state."""
        if expected_revision != self._plan_revision:
            raise ValueError(
                f"Expected plan revision {self._plan_revision}, got {expected_revision}"
            )

    def _require_active_task(self, task_id: str) -> TaskRecord:
        """Resolve the running Task named by one execution result."""
        if self._active_task_id != task_id:
            raise ValueError("Task execution result does not match the active Task")
        return next(item for item in self._tasks if item.task_id == task_id)

    def _find_milestone(self, milestone_id: str) -> MilestoneRecord:
        """Resolve a Milestone identity or reject an invented reference."""
        try:
            return next(
                item for item in self._milestones if item.milestone_id == milestone_id
            )
        except StopIteration as exc:
            raise ValueError(f"Unknown milestone: {milestone_id}") from exc

    def _replace_task(self, task: TaskRecord, **changes: object) -> None:
        """Replace one frozen Task while keeping its stable list position."""
        index = self._tasks.index(task)
        self._tasks[index] = task.model_copy(update=changes)

    def _reject_unchanged_failed_retry(self, proposal: TaskProposal) -> None:
        """Require an explicit new reason and prior Attempt for an exact retry."""
        failed_tasks = [item for item in self._tasks if item.status == "failed"]
        for failed in failed_tasks:
            same_work = (
                failed.objective == proposal.objective
                and failed.purpose == proposal.purpose
                and failed.success_criteria == proposal.success_criteria
            )
            if not same_work:
                continue
            failed_attempt_ids = {
                item.attempt_id
                for item in self._attempts
                if item.task_id == failed.task_id and item.outcome == "failed"
            }
            if (
                proposal.retry_reason is None
                or not failed_attempt_ids.intersection(proposal.related_attempt_ids)
            ):
                raise ValueError(
                    "An unchanged failed Task requires a new retry reason and its failed Attempt"
                )
