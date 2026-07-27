"""Deterministic grouping of confirmed parameter solutions."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from restscope.agent.parameter_patch import PatchGroupTask
from restscope.testing import OperationGeneratorConfig

from .schemas import ActionableFailure


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class PatchGroupingResult(_Model):
    """Result of deterministic grouping, including any deferred solution items."""
    status: Literal["grouped", "inconclusive"]
    tasks: list[PatchGroupTask] = Field(default_factory=list)
    deferred_item_ids: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class PatchGroupPlanner:
    """Build stable Patch Groups from immutable actionable solutions.

    Inputs are vertices in a small undirected graph. Two inputs are connected
    when a confirmed solution says their values interact through a same-request
    Constraint. Connected components become Patch Groups, guaranteeing that one
    input belongs to only one group without another LLM call.
    """

    def group(
        self,
        *,
        actionable_failures: list[ActionableFailure],
        config: OperationGeneratorConfig,
    ) -> PatchGroupingResult:
        """Group first-seen inputs with union-find and emit stable ``G1…Gn`` IDs."""
        del config
        if not actionable_failures:
            return PatchGroupingResult(
                status="inconclusive",
                errors=["No actionable failures were supplied"],
            )

        input_order = list(
            dict.fromkeys(
                input_handle
                for item in actionable_failures
                for input_handle in item.affected_inputs
            )
        )
        # `parent` implements union-find. Path compression in `find` keeps
        # repeated lookups cheap and preserves the first-seen component root.
        parent = {input_handle: input_handle for input_handle in input_order}

        def find(input_handle: str) -> str:
            root = input_handle
            while parent[root] != root:
                root = parent[root]
            while parent[input_handle] != input_handle:
                next_input = parent[input_handle]
                parent[input_handle] = root
                input_handle = next_input
            return root

        def union(left: str, right: str) -> None:
            left_root = find(left)
            right_root = find(right)
            if left_root != right_root:
                parent[right_root] = left_root

        # Multiple affected inputs are not automatically coupled. Only an
        # explicit interaction note authorizes a Constraint relationship.
        for item in actionable_failures:
            if not item.interaction_notes or len(item.affected_inputs) < 2:
                continue
            first = item.affected_inputs[0]
            for input_handle in item.affected_inputs[1:]:
                union(first, input_handle)

        components: dict[str, list[str]] = {}
        for input_handle in input_order:
            components.setdefault(find(input_handle), []).append(input_handle)

        # Dictionaries preserve insertion order, so groups follow first
        # appearance in the actionable diagnosis.
        tasks: list[PatchGroupTask] = []
        for index, inputs in enumerate(components.values(), start=1):
            item_ids = [
                item.item_id
                for item in actionable_failures
                if set(item.affected_inputs).intersection(inputs)
            ]
            tasks.append(
                _build_task(
                    group_id=f"G{index}",
                    inputs=inputs,
                    item_ids=item_ids,
                    by_item={
                        item.item_id: item for item in actionable_failures
                    },
                )
            )
        return PatchGroupingResult(status="grouped", tasks=tasks)


def _build_task(
    *,
    group_id: str,
    inputs: list[str],
    item_ids: list[str],
    by_item: dict[str, ActionableFailure],
) -> PatchGroupTask:
    """
    Build task for the run-local Operation Smoke diagnosis and candidate workflow.

    This private helper keeps one transformation or policy decision explicit so the
    surrounding orchestration remains readable.
    """
    items = [by_item[item_id] for item_id in item_ids]
    requirements: list[str] = []
    hints: list[object] = []
    notes: list[str] = []
    roots: list[str] = []
    causes: list[str] = []
    for item in items:
        if item.cause not in causes:
            causes.append(item.cause)
        for root in item.root_failure_refs:
            if root not in roots:
                roots.append(root)
        for solution in item.solutions:
            if solution.input not in inputs:
                continue
            requirements.append(
                f"{solution.input}: {solution.desired_behavior}"
            )
            for value in solution.candidate_values:
                if value not in hints:
                    hints.append(value)
            if solution.candidate_range is not None:
                for value in solution.candidate_range:
                    if value not in hints:
                        hints.append(value)
        for note in item.interaction_notes:
            if note not in notes:
                notes.append(note)
    return PatchGroupTask(
        group_id=group_id,
        item_ids=item_ids,
        root_failure_refs=roots,
        inputs=inputs,
        objective="; ".join(causes),
        requirements=requirements,
        candidate_hints=hints,
        interaction_notes=notes,
    )
