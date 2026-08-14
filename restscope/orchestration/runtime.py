"""Run RESTScope's complete Plan, Execute, Record, and Replan cycle.

This is the only module that knows the long-task control flow. It calls the
existing Harness System Agent lifecycle for each Orchestrator decision and each
Task Executor run, then asks :class:`TaskLedger` to validate every transition.
"""

from __future__ import annotations

from collections.abc import Callable

from restscope.agent import SystemAgentResult, SystemAgentTask
from restscope.agent.contracts import AgentResultStatus
from restscope.context import CompactTextWriter
from restscope.harness import HarnessRuntime

from .ledger import TaskLedger
from .models import (
    AttemptRecord,
    CompleteDecision,
    DispatchTaskDecision,
    GoalContract,
    GoalCriterion,
    MilestoneRecord,
    OrchestrationObservation,
    OrchestrationResult,
    OrchestrationSessionRecord,
    OrchestratorDecision,
    PlanRevisionRecord,
    ReplanDecision,
    TaskExecutionResult,
    TaskRecord,
)

_MISSION = (
    "Explore the target REST API dynamically, confirm useful happy paths, perform "
    "worthwhile exceptional testing, and report only replay-confirmed Bug Oracle "
    "findings. Preserve evidence and adapt future work as each result is learned."
)


class OrchestrationRuntime:
    """Coordinate fresh planning and execution roots around one in-memory Ledger."""

    def __init__(
        self,
        system_agents: HarnessRuntime,
        *,
        observe: Callable[[OrchestrationObservation], None] | None = None,
    ) -> None:
        """Accept the Harness lifecycle and an optional fail-open read-only sink.

        Args:
            system_agents: Harness entrypoint for fresh registered System roots.
            observe: Optional UI-facing receiver for complete immutable state.
                Receiver failures never change testing or Ledger behavior.
        """
        self._system_agents = system_agents
        self._observe = observe
        self._observation_revision = 0
        self._observed_sessions: list[OrchestrationSessionRecord] = []

    def run(self, focus: str | None = None) -> OrchestrationResult:
        """Run until the Orchestrator semantically completes the fixed Goal.

        Args:
            focus: Optional run emphasis or restriction appended to the fixed
                product mission. It cannot replace that mission.

        Returns:
            The final summary, unresolved issues, immutable Goal, and Ledger
            snapshot. There is deliberately no artificial round limit.

        Raises:
            ValueError: If a System Agent fails or returns a decision/result
                that violates the current Ledger contract.
        """
        # One runtime normally serves one App start, but resetting here keeps a
        # second explicit run from inheriting UI identities or revisions.
        self._observation_revision = 0
        self._observed_sessions.clear()
        goal = _build_goal(focus)
        ledger = TaskLedger(goal)
        first_decision = True
        self._publish_observation(ledger)

        while True:
            decision, orchestrator_result = self._run_orchestrator(ledger)
            if first_decision and not isinstance(decision, ReplanDecision):
                raise ValueError("The first Orchestrator decision must be replan")
            first_decision = False

            if isinstance(decision, ReplanDecision):
                ledger.apply_replan(decision)
                self._record_orchestrator(orchestrator_result, decision.kind)
                self._publish_observation(ledger)
                continue
            if isinstance(decision, DispatchTaskDecision):
                task = ledger.dispatch(
                    decision.task,
                    expected_revision=decision.expected_plan_revision,
                )
                self._record_orchestrator(
                    orchestrator_result,
                    decision.kind,
                    task_id=task.task_id,
                )
                self._publish_observation(ledger)
                try:
                    execution_result, executor_result = self._run_task_executor(
                        goal,
                        ledger,
                        task,
                    )
                except _TaskExecutorLifecycleError as exc:
                    # A failed fresh root is still material history. Recording
                    # it lets the Orchestrator choose a changed recovery task.
                    attempt = ledger.append_failure(
                        task.task_id,
                        failure_code=exc.code,
                        failure_message=exc.message,
                    )
                    self._record_task_executor(
                        session_id=exc.session_id,
                        status=exc.status,
                        task_id=task.task_id,
                        attempt_id=attempt.attempt_id,
                    )
                    self._publish_observation(ledger)
                except RuntimeError as exc:
                    # Provider and Harness exceptions are terminal for this
                    # root, not for the outer Goal. Keep only a bounded safe
                    # explanation; stack traces never enter Agent context.
                    message = str(exc).strip() or type(exc).__name__
                    ledger.append_failure(
                        task.task_id,
                        failure_code="task_executor_lifecycle_error",
                        failure_message=message[:2_000],
                    )
                    self._publish_observation(ledger)
                else:
                    attempt = ledger.append_attempt(execution_result)
                    self._record_task_executor(
                        session_id=executor_result.session_id,
                        status=executor_result.status,
                        task_id=task.task_id,
                        attempt_id=attempt.attempt_id,
                    )
                    self._publish_observation(ledger)
                continue

            final_snapshot = ledger.complete(decision)
            self._record_orchestrator(orchestrator_result, decision.kind)
            self._publish_observation(ledger)
            return OrchestrationResult(
                summary=decision.summary,
                unresolved=decision.unresolved,
                goal=goal,
                ledger=final_snapshot,
            )

    def _run_orchestrator(
        self, ledger: TaskLedger
    ) -> tuple[
        ReplanDecision | DispatchTaskDecision | CompleteDecision,
        SystemAgentResult,
    ]:
        """Request one exclusive decision and retain its exact root identity."""
        snapshot = ledger.snapshot()
        # The Harness uses these prompt-local identities to reject invented
        # revisions, Milestones, Attempts, and Goal criteria before return.
        aliases = (
            f"revision_{snapshot.plan_revision}",
            *(item.criterion_id for item in ledger.goal.success_criteria),
            *(
                item.milestone_id
                for item in snapshot.milestones
                if item.status == "pending"
            ),
            *(item.attempt_id for item in snapshot.attempts[-20:]),
        )
        task = SystemAgentTask(
            objective=_render_orchestrator_task(ledger),
            allowed_result_aliases=aliases,
        )
        result = self._system_agents.run_system_agent("orchestrator", task)
        output = _require_completed_output(result, profile_name="orchestrator")
        return OrchestratorDecision.model_validate(output).root, result

    def _run_task_executor(
        self, goal: GoalContract, ledger: TaskLedger, task: TaskRecord
    ) -> tuple[TaskExecutionResult, SystemAgentResult]:
        """Run one fresh Task Executor and retain its exact root identity."""
        system_task = SystemAgentTask(
            objective=_render_task_execution(goal, ledger, task),
            allowed_result_aliases=(
                task.task_id,
                *(criterion.criterion_id for criterion in task.success_criteria),
            ),
        )
        result = self._system_agents.run_system_agent("task-executor", system_task)
        if result.status != "completed" or result.output is None:
            code = result.status
            message = "Task Executor ended without a structured result."
            if result.error is not None:
                code = result.error.code
                message = result.error.message
            raise _TaskExecutorLifecycleError(
                code=code,
                message=message,
                session_id=result.session_id,
                status=result.status,
            )
        output = result.output
        return TaskExecutionResult.model_validate(output), result

    def _record_orchestrator(
        self,
        result: SystemAgentResult,
        decision_kind: str,
        *,
        task_id: str | None = None,
    ) -> None:
        """Remember one accepted Orchestrator decision without copying its output."""
        sequence = sum(
            item.role == "orchestrator" for item in self._observed_sessions
        ) + 1
        self._observed_sessions.append(
            OrchestrationSessionRecord(
                session_id=result.session_id,
                profile_name=result.profile_name,
                role="orchestrator",
                sequence=sequence,
                status=result.status,
                decision_kind=decision_kind,
                task_id=task_id,
            )
        )

    def _record_task_executor(
        self,
        *,
        session_id: str,
        status: AgentResultStatus,
        task_id: str,
        attempt_id: str,
    ) -> None:
        """Link one terminal Task Executor root to its immutable Attempt."""
        sequence = sum(
            item.role == "task_executor" for item in self._observed_sessions
        ) + 1
        self._observed_sessions.append(
            OrchestrationSessionRecord(
                session_id=session_id,
                profile_name="task-executor",
                role="task_executor",
                sequence=sequence,
                status=status,
                task_id=task_id,
                attempt_id=attempt_id,
            )
        )

    def _publish_observation(self, ledger: TaskLedger) -> None:
        """Send one complete immutable replacement without affecting the run."""
        if self._observe is None:
            return
        self._observation_revision += 1
        observation = OrchestrationObservation(
            revision=self._observation_revision,
            goal=ledger.goal,
            ledger=ledger.snapshot(),
            sessions=tuple(self._observed_sessions),
        )
        try:
            self._observe(observation)
        except Exception:  # noqa: BLE001
            # The loopback viewer is diagnostic only. A broken viewer must not
            # change which API work runs or what the Ledger accepts.
            return


def _build_goal(focus: str | None) -> GoalContract:
    """Construct the fixed mission and stable completion questions for one run."""
    return GoalContract(
        mission=_MISSION,
        focus=focus,
        success_criteria=(
            GoalCriterion(
                criterion_id="goal_1",
                description="Relevant testable operations have reproducible happy-path evidence.",
            ),
            GoalCriterion(
                criterion_id="goal_2",
                description="Worthwhile exceptional behavior has been tested or left explicit.",
            ),
            GoalCriterion(
                criterion_id="goal_3",
                description="Only replay-confirmed unexpected responses are reported as Bugs.",
            ),
        ),
    )


def _render_orchestrator_task(ledger: TaskLedger) -> str:
    """Project current state as bounded Markdown without prior Agent transcripts."""
    snapshot = ledger.snapshot()
    tasks_by_id = {item.task_id: item for item in snapshot.tasks}
    milestones_by_id = {item.milestone_id: item for item in snapshot.milestones}
    writer = CompactTextWriter(max_value_chars=1_000)
    writer.section("Required decision")
    writer.text(
        "Instruction",
        "Return exactly one replan, dispatch_task, or complete decision for this state.",
    )
    writer.section("Goal contract")
    writer.text("Mission", ledger.goal.mission)
    writer.text("Run focus", ledger.goal.focus)
    writer.detail(
        "Goal criteria",
        {
            item.criterion_id: item.description for item in ledger.goal.success_criteria
        },
    )
    writer.section("Current Ledger")
    writer.text("Plan revision", snapshot.plan_revision)
    writer.text("Run status", snapshot.run_status)
    for milestone in snapshot.milestones:
        if milestone.status == "pending":
            writer.record(
                milestone.milestone_id,
                **_milestone_projection(milestone),
            )

    # The newest accepted plan change and execution outcome are required causal
    # state. Earlier records remain useful, but the Writer may omit them whole
    # instead of clipping the final instruction or half of one Attempt.
    if snapshot.plan_revisions:
        latest_revision = snapshot.plan_revisions[-1]
        writer.section("Latest Plan Revision")
        writer.record(
            f"revision_{latest_revision.plan_revision}",
            **_plan_revision_projection(latest_revision),
        )
    if snapshot.attempts:
        latest_attempt = snapshot.attempts[-1]
        writer.section("Latest Attempt")
        writer.record(
            latest_attempt.attempt_id,
            **_attempt_projection(
                latest_attempt,
                tasks_by_id=tasks_by_id,
                milestones_by_id=milestones_by_id,
            ),
        )

    writer.section("Optional recent history")
    for revision in reversed(snapshot.plan_revisions[-10:-1]):
        writer.record(
            f"revision_{revision.plan_revision}",
            required=False,
            **_plan_revision_projection(revision),
        )
    for attempt in reversed(snapshot.attempts[-20:-1]):
        writer.record(
            attempt.attempt_id,
            required=False,
            **_attempt_projection(
                attempt,
                tasks_by_id=tasks_by_id,
                milestones_by_id=milestones_by_id,
            ),
        )
    historical_milestones = tuple(
        item for item in snapshot.milestones if item.status != "pending"
    )[-10:]
    for milestone in reversed(historical_milestones):
        writer.record(
            milestone.milestone_id,
            required=False,
            **_milestone_projection(milestone),
        )
    return writer.render(18_000).text


def _render_task_execution(goal: GoalContract, ledger: TaskLedger, task: TaskRecord) -> str:
    """Render one bounded assignment plus only explicitly related Attempt history."""
    snapshot = ledger.snapshot()
    tasks_by_id = {item.task_id: item for item in snapshot.tasks}
    milestones_by_id = {item.milestone_id: item for item in snapshot.milestones}
    milestone = next(
        item for item in snapshot.milestones if item.milestone_id == task.milestone_id
    )
    writer = CompactTextWriter(max_value_chars=1_000)
    writer.section("Bounded Goal")
    writer.text("Mission", goal.mission)
    writer.text("Run focus", goal.focus)
    writer.section("Current Milestone")
    writer.record(
        milestone.milestone_id,
        title=milestone.title,
        purpose=milestone.purpose,
        success_criteria=milestone.success_criteria,
    )
    writer.section("Single Task Executor assignment")
    writer.record(
        task.task_id,
        objective=task.objective,
        purpose=task.purpose,
        retry_reason=task.retry_reason,
        success_criteria={
            item.criterion_id: item.description for item in task.success_criteria
        },
    )
    writer.section("Related Attempt history")
    writer.detail(
        "Attempts",
        [
            _attempt_projection(
                item,
                tasks_by_id=tasks_by_id,
                milestones_by_id=milestones_by_id,
            )
            for item in ledger.related_attempts(task)
        ],
    )
    writer.section("Required result")
    writer.text(
        "Instruction",
        "Execute only this Task and report each criterion exactly once; do not plan the next Task.",
    )
    return writer.render(18_000).text


def _milestone_projection(milestone: MilestoneRecord) -> dict[str, object]:
    """Keep one Milestone prompt entry small and free of implementation details."""
    return {
        "revision": milestone.plan_revision,
        "title": milestone.title,
        "purpose": milestone.purpose,
        "success_criteria": milestone.success_criteria,
        "status": milestone.status,
    }


def _plan_revision_projection(revision: PlanRevisionRecord) -> dict[str, object]:
    """Retain why one plan changed and which Milestones it affected."""
    return {
        "reason": revision.reason,
        "completed_milestone_ids": revision.completed_milestone_ids,
        "superseded_milestone_ids": revision.superseded_milestone_ids,
        "created_milestone_ids": revision.created_milestone_ids,
    }


def _attempt_projection(
    attempt: AttemptRecord,
    *,
    tasks_by_id: dict[str, TaskRecord],
    milestones_by_id: dict[str, MilestoneRecord],
) -> dict[str, object]:
    """Join one outcome to the Task and Milestone that explain why it ran."""
    task = tasks_by_id[attempt.task_id]
    milestone = milestones_by_id[task.milestone_id]
    result = attempt.result
    return {
        "task_id": attempt.task_id,
        "plan_revision": attempt.plan_revision,
        "milestone_id": milestone.milestone_id,
        "milestone_title": milestone.title,
        "task_objective": task.objective,
        "task_purpose": task.purpose,
        "task_success_criteria": task.success_criteria,
        "retry_reason": task.retry_reason,
        "outcome": attempt.outcome,
        "criteria": () if result is None else result.criteria,
        "findings": () if result is None else result.findings,
        "unresolved_issues": () if result is None else result.unresolved_issues,
        "target_state_changes": (
            () if result is None else result.target_state_changes
        ),
        "failure_code": attempt.failure_code,
        "failure_message": attempt.failure_message,
    }


def _require_completed_output(
    result: SystemAgentResult, *, profile_name: str
) -> dict[str, object]:
    """Translate a terminal System Agent lifecycle result into one clear failure."""
    if result.status != "completed" or result.output is None:
        detail = "unknown lifecycle failure"
        if result.error is not None:
            detail = f"{result.error.code}: {result.error.message}"
        raise ValueError(f"{profile_name} System Agent failed: {detail}")
    return result.output


class _TaskExecutorLifecycleError(RuntimeError):
    """Carry one bounded Task Executor failure into the immutable Ledger."""

    def __init__(
        self,
        *,
        code: str,
        message: str,
        session_id: str,
        status: AgentResultStatus,
    ) -> None:
        """Preserve safe failure details and the exact failed root identity."""
        super().__init__(message)
        self.code = code[:120]
        self.message = message[:2_000]
        self.session_id = session_id
        self.status = status
