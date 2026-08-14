"""Bind Orchestration decisions to Harness System Agent result contracts.

The App registers these models and callbacks with the generic Harness. JSON
Schema rejects malformed outputs, while task-local validators check revision,
Task, Milestone, Attempt, and criterion identities before the runtime can ask
the Ledger to mutate state.
"""

from __future__ import annotations

from pydantic import BaseModel

from restscope.agent import SystemAgentTask

from .models import MainTaskResult, OrchestratorDecision


def orchestrator_output_schema(_task: SystemAgentTask) -> dict[str, object]:
    """Return the closed discriminated-union schema for one outer decision."""
    return OrchestratorDecision.model_json_schema()


def validate_orchestrator_output(
    output: BaseModel,
    task: SystemAgentTask,
) -> tuple[str, ...]:
    """Check task-local identities that static JSON Schema cannot know.

    ``allowed_result_aliases`` is ordered as the expected revision, fixed Goal
    criteria, current Milestones, then retained Attempts. The rendered prompt
    explains those identities; this compact side channel lets the Harness give
    precise correction feedback without parsing prompt text.
    """
    decision = OrchestratorDecision.model_validate(output).root
    if not task.allowed_result_aliases:
        return ("Orchestrator task did not declare its current revision.",)
    revision_alias = task.allowed_result_aliases[0]
    revision_value = revision_alias.removeprefix("revision_")
    if not revision_alias.startswith("revision_") or not revision_value.isdigit():
        return ("Orchestrator task declared an invalid revision identity.",)
    expected_revision = int(revision_value)
    errors: list[str] = []
    if decision.expected_plan_revision != expected_revision:
        errors.append(
            f"expected_plan_revision must be {expected_revision} for this Ledger view."
        )

    aliases = set(task.allowed_result_aliases[1:])
    if decision.kind == "replan":
        unknown_completed = set(decision.completed_milestone_ids) - aliases
        if unknown_completed:
            errors.append(
                "Unknown completed Milestone IDs: "
                + ", ".join(sorted(unknown_completed))
                + "."
            )
    elif decision.kind == "dispatch_task":
        if decision.task.milestone_id not in aliases:
            errors.append("The dispatched Milestone is not present in this Ledger view.")
        unknown_attempts = set(decision.task.related_attempt_ids) - aliases
        if unknown_attempts:
            errors.append(
                "Unknown related Attempt IDs: " + ", ".join(sorted(unknown_attempts)) + "."
            )
    elif decision.kind == "complete":
        offered_goal_ids = {item for item in aliases if item.startswith("goal_")}
        returned_goal_ids = tuple(item.criterion_id for item in decision.goal_criteria)
        if (
            len(returned_goal_ids) != len(set(returned_goal_ids))
            or set(returned_goal_ids) != offered_goal_ids
        ):
            errors.append("Completion must assess every offered Goal criterion exactly once.")
    return tuple(errors)


def worker_output_schema(_task: SystemAgentTask) -> dict[str, object]:
    """Return the closed result schema for one bounded Main Worker task."""
    return MainTaskResult.model_json_schema()


def validate_worker_output(
    output: BaseModel,
    task: SystemAgentTask,
) -> tuple[str, ...]:
    """Require the active Task and every offered criterion exactly once."""
    result = MainTaskResult.model_validate(output)
    if not task.allowed_result_aliases:
        return ("Worker task did not declare its active Task identity.",)
    expected_task_id, *expected_criterion_ids = task.allowed_result_aliases
    errors: list[str] = []
    if result.task_id != expected_task_id:
        errors.append(f"task_id must be {expected_task_id} for this Worker call.")
    actual_ids = tuple(item.criterion_id for item in result.criteria)
    if (
        len(actual_ids) != len(set(actual_ids))
        or set(actual_ids) != set(expected_criterion_ids)
    ):
        errors.append("Worker must report every offered criterion exactly once.")
    if result.outcome == "completed" and any(
        item.status != "met" for item in result.criteria
    ):
        errors.append("A completed Worker result requires every criterion to be met.")
    return tuple(errors)
