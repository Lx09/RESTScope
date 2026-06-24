"""Working memory from agent_tasks."""

from __future__ import annotations

from restscope.db.repositories import AgentTaskRepository

from ..schemas import MemoryItem


class WorkingMemoryStore:
    def __init__(self, task_repo: AgentTaskRepository) -> None:
        self.task_repo = task_repo

    def get_current_task_memory(self, task_id: str) -> list[MemoryItem]:
        task = self.task_repo.require(task_id)
        content = (
            f"Task is in {task.state} state, cycle_index={task.cycle_index}. "
            f"Selected operations: {', '.join(task.selected_operation_ids) or 'none'}."
        )
        return [
            MemoryItem(
                id=f"mem_working_{task.id}",
                kind="working",
                schema_id=task.schema_id,
                task_id=task.id,
                title="Current task state",
                content=content,
                structured={
                    "state": task.state,
                    "goal": task.goal_json,
                    "budget": task.budget_json,
                    "cycle_index": task.cycle_index,
                    "selected_operation_ids": task.selected_operation_ids,
                    "current_hypotheses": task.current_hypotheses,
                    "current_check_ids": task.current_check_ids,
                    "blockers": task.blockers_json,
                    "last_error": task.last_error,
                },
                importance=0.95,
                confidence=1.0,
                relevance_score=1.0,
                source_table="agent_tasks",
                source_id=task.id,
            )
        ]
