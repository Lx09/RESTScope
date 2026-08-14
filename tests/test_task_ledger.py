"""Protect immutable history and legal transitions in the Task Ledger."""

from __future__ import annotations

import pytest

from restscope.orchestration.ledger import TaskLedger
from restscope.orchestration.models import (
    GoalContract,
    GoalCriterion,
    MainTaskResult,
    ReplanDecision,
    TaskCriterion,
    TaskProposal,
)


def _ledger_with_plan() -> TaskLedger:
    """Create one planned Ledger for focused transition scenarios."""
    ledger = TaskLedger(
        GoalContract(
            mission="Explore the target.",
            success_criteria=(
                GoalCriterion(criterion_id="goal_1", description="Explore safely."),
            ),
        )
    )
    ledger.apply_replan(
        ReplanDecision(
            kind="replan",
            expected_plan_revision=0,
            reason="Create the first bounded milestone.",
            milestones=(
                {
                    "title": "Discover pets",
                    "purpose": "Find a happy path.",
                    "success_criteria": ("GET /pets returns 2xx.",),
                },
            ),
        )
    )
    return ledger


def _task() -> TaskProposal:
    """Return the one assignment shared by validation scenarios."""
    return TaskProposal(
        milestone_id="milestone_1",
        objective="Run GET /pets.",
        purpose="Confirm the happy path.",
        success_criteria=(
            TaskCriterion(
                criterion_id="criterion_1",
                description="The response is 2xx.",
            ),
        ),
    )


def test_no_op_replan_preserves_revision_and_history() -> None:
    """Repeating the current future plan cannot manufacture a new revision."""
    ledger = _ledger_with_plan()
    before = ledger.snapshot()

    with pytest.raises(ValueError, match="materially change"):
        ledger.apply_replan(
            ReplanDecision(
                kind="replan",
                expected_plan_revision=1,
                reason="Repeat it unchanged.",
                milestones=(
                    {
                        "title": "Discover pets",
                        "purpose": "Find a happy path.",
                        "success_criteria": ("GET /pets returns 2xx.",),
                    },
                ),
            )
        )

    assert ledger.snapshot() == before


def test_invalid_worker_criteria_do_not_change_the_ledger() -> None:
    """Missing criterion results fail before a Task or Attempt is replaced."""
    ledger = _ledger_with_plan()
    running = ledger.dispatch(_task(), expected_revision=1)
    before = ledger.snapshot()

    with pytest.raises(ValueError, match="every Task criterion exactly once"):
        ledger.append_attempt(
            MainTaskResult(
                task_id=running.task_id,
                outcome="partial",
                criteria=(
                    {
                        "criterion_id": "criterion_2",
                        "status": "unknown",
                        "explanation": "The expected criterion was omitted.",
                    },
                ),
            )
        )

    assert ledger.snapshot() == before


def test_lifecycle_failure_appends_history_before_future_replan() -> None:
    """A failed Worker root becomes immutable evidence instead of disappearing."""
    ledger = _ledger_with_plan()
    running = ledger.dispatch(_task(), expected_revision=1)

    attempt = ledger.append_failure(
        running.task_id,
        failure_code="provider_failed",
        failure_message="The model provider stopped.",
    )

    snapshot = ledger.snapshot()
    assert attempt.outcome == "failed"
    assert snapshot.tasks[0].status == "failed"
    assert snapshot.attempts == (attempt,)


def test_unchanged_failed_task_requires_new_reason_and_failed_attempt() -> None:
    """Recovery cannot silently repeat the same failed operation forever."""
    ledger = _ledger_with_plan()
    first = ledger.dispatch(_task(), expected_revision=1)
    failed = ledger.append_failure(
        first.task_id,
        failure_code="provider_failed",
        failure_message="The provider stopped.",
    )

    with pytest.raises(ValueError, match="new retry reason"):
        ledger.dispatch(_task(), expected_revision=1)

    retry = _task().model_copy(
        update={
            "related_attempt_ids": (failed.attempt_id,),
            "retry_reason": "The provider recovered and the target is unchanged.",
        }
    )
    assert ledger.dispatch(retry, expected_revision=1).task_id == "task_2"


def test_replan_can_complete_then_reopen_a_milestone_without_rewriting_history() -> None:
    """A later revision may supersede prior conclusions while preserving them."""
    ledger = _ledger_with_plan()
    ledger.apply_replan(
        ReplanDecision(
            kind="replan",
            expected_plan_revision=1,
            reason="The first milestone is satisfied; move forward.",
            completed_milestone_ids=("milestone_1",),
            milestones=(
                {
                    "title": "Test errors",
                    "purpose": "Exercise exceptional behavior.",
                    "success_criteria": ("One invalid request is observed.",),
                },
            ),
        )
    )
    ledger.apply_replan(
        ReplanDecision(
            kind="replan",
            expected_plan_revision=2,
            reason="New evidence invalidated the earlier happy-path conclusion.",
            milestones=(
                {
                    "title": "Recheck pets",
                    "purpose": "Reassess the earlier conclusion.",
                    "success_criteria": ("GET /pets is reproduced again.",),
                    "supersedes_milestone_id": "milestone_1",
                },
            ),
        )
    )

    snapshot = ledger.snapshot()
    assert snapshot.milestones[0].status == "completed"
    assert snapshot.milestones[0].title == "Discover pets"
    assert snapshot.milestones[-1].supersedes_milestone_id == "milestone_1"
    assert snapshot.plan_revision == 3
