"""FAST grouping of confirmed parameter solutions into Patch Group tasks."""

from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from restscope.agent.parameter_patch import PatchGroupTask
from restscope.llm import (
    LLMClient,
    LLMMessage,
    LLMModelConfig,
    LLMRequest,
    LLMResponse,
    OutputValidator,
)
from restscope.observability import TracingRuntime
from restscope.testing import OperationGeneratorConfig

from .schemas import ActionableFailure


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class PatchGroupShape(_Model):
    item_ids: list[str] = Field(min_length=1, max_length=100)
    inputs: list[str] = Field(min_length=1, max_length=100)


class PatchGroupingDecision(_Model):
    groups: list[PatchGroupShape] = Field(default_factory=list, max_length=100)
    deferred_item_ids: list[str] = Field(default_factory=list, max_length=100)


class PatchGroupingResult(_Model):
    status: Literal["grouped", "inconclusive"]
    tasks: list[PatchGroupTask] = Field(default_factory=list)
    deferred_item_ids: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class PatchGroupPlanner:
    """Group immutable actionable solutions without changing their meaning."""

    def __init__(
        self,
        *,
        client: LLMClient,
        model: LLMModelConfig,
        validator: OutputValidator | None = None,
        tracing_runtime: TracingRuntime | None = None,
    ) -> None:
        self.client = client
        self.model = model
        self.validator = validator or OutputValidator()
        self.tracing_runtime = tracing_runtime or TracingRuntime.disabled()

    def group(
        self,
        *,
        actionable_failures: list[ActionableFailure],
        config: OperationGeneratorConfig,
    ) -> PatchGroupingResult:
        if not actionable_failures:
            return PatchGroupingResult(
                status="inconclusive",
                errors=["No actionable failures were supplied"],
            )
        system = (
            "Group confirmed parameter solutions for one operation. "
            "You may only combine supplied item_ids and inputs. Do not add, "
            "remove, reinterpret, or rewrite a root cause or desired behavior. "
            "Each input must appear in exactly one group unless every item "
            "using it is explicitly deferred. Inputs that must participate in "
            "one same-request constraint belong in the same group. Return JSON."
        )
        user = json.dumps(
            {
                "operation": (
                    f"{config.snapshot.method} {config.snapshot.path}"
                ),
                "actionable_failures": [
                    item.model_dump(mode="json")
                    for item in actionable_failures
                ],
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        )
        messages = [
            LLMMessage(role="system", content=system),
            LLMMessage(role="user", content=user),
        ]
        response = self._invoke(messages)
        decision, errors = self._parse_and_validate(
            response,
            actionable_failures=actionable_failures,
        )
        if errors:
            repaired_messages = [
                *messages,
                LLMMessage(role="assistant", content=_response_json(response)),
                LLMMessage(
                    role="user",
                    content=(
                        "The grouping could not be used:\n"
                        + "\n".join(f"- {error}" for error in errors[:20])
                        + "\nReturn one complete corrected grouping."
                    ),
                ),
            ]
            response = self._invoke(repaired_messages)
            decision, errors = self._parse_and_validate(
                response,
                actionable_failures=actionable_failures,
            )
        if decision is None or errors:
            return PatchGroupingResult(
                status="inconclusive",
                errors=errors,
            )
        by_item = {item.item_id: item for item in actionable_failures}
        tasks = [
            _build_task(
                group_id=f"G{index}",
                shape=shape,
                by_item=by_item,
            )
            for index, shape in enumerate(decision.groups, start=1)
        ]
        return PatchGroupingResult(
            status="grouped" if tasks else "inconclusive",
            tasks=tasks,
            deferred_item_ids=decision.deferred_item_ids,
        )

    def _invoke(self, messages: list[LLMMessage]) -> LLMResponse:
        return self.client.invoke(
            LLMRequest(
                provider=self.model.provider,
                model=self.model.model,
                messages=messages,
                temperature=self.model.temperature,
                max_tokens=self.model.max_tokens,
                response_format="json",
                tools=[],
                tool_choice="none",
                timeout_seconds=self.model.timeout_seconds,
                reasoning=self.model.reasoning,
                metadata={"role": "operation_smoke_patch_grouping"},
            )
        )

    def _parse_and_validate(
        self,
        response: LLMResponse,
        *,
        actionable_failures: list[ActionableFailure],
    ) -> tuple[PatchGroupingDecision | None, list[str]]:
        parsed = self.validator.validate(
            response=response,
            output_model=PatchGroupingDecision,
        )
        if not parsed.valid:
            return None, [
                (
                    f"{issue.location}: {issue.message}"
                    if issue.location
                    else issue.message
                )
                for issue in parsed.errors[:20]
            ]
        decision = PatchGroupingDecision.model_validate(
            parsed.validated_object
        )
        return decision, _validate_grouping(
            decision,
            actionable_failures=actionable_failures,
        )


def _validate_grouping(
    decision: PatchGroupingDecision,
    *,
    actionable_failures: list[ActionableFailure],
) -> list[str]:
    errors: list[str] = []
    by_item = {item.item_id: item for item in actionable_failures}
    known_inputs = {
        input_handle
        for item in actionable_failures
        for input_handle in item.affected_inputs
    }
    grouped_inputs = [
        input_handle
        for group in decision.groups
        for input_handle in group.inputs
    ]
    deferred = set(decision.deferred_item_ids)
    for group in decision.groups:
        if len(set(group.inputs)) != len(group.inputs):
            errors.append("A group cannot repeat an input.")
        if len(set(group.item_ids)) != len(group.item_ids):
            errors.append("A group cannot repeat an item_id.")
        for input_handle in group.inputs:
            if input_handle not in known_inputs:
                errors.append(f"{input_handle} was not supplied.")
        for item_id in group.item_ids:
            if item_id not in by_item:
                errors.append(f"{item_id} was not supplied.")
        expected_items = {
            item.item_id
            for item in actionable_failures
            if any(
                solution.input in group.inputs
                for solution in item.solutions
            )
        }
        if set(group.item_ids) != expected_items:
            errors.append(
                "Each group must include exactly the items whose solutions "
                f"use its inputs; expected={sorted(expected_items)}."
            )
    for input_handle in set(grouped_inputs):
        if grouped_inputs.count(input_handle) != 1:
            errors.append(
                f"{input_handle} must appear in exactly one Patch Group."
            )
    for input_handle in known_inputs:
        if input_handle in grouped_inputs:
            continue
        owners = {
            item.item_id
            for item in actionable_failures
            if input_handle in item.affected_inputs
        }
        if not owners.issubset(deferred):
            errors.append(
                f"{input_handle} must appear in one Patch Group or all of "
                "its actionable items must be deferred."
            )
    for item_id in deferred:
        if item_id not in by_item:
            errors.append(f"{item_id} was not supplied.")
    for item in actionable_failures:
        if item.interaction_notes and item.item_id not in deferred:
            interaction_group_indexes = {
                index
                for index, group in enumerate(decision.groups)
                if set(item.affected_inputs).intersection(group.inputs)
            }
            if len(interaction_group_indexes) != 1:
                errors.append(
                    f"{item.item_id} has constraint-linked inputs that must "
                    "remain in the same Patch Group."
                )
        covered = any(
            input_handle in grouped_inputs
            for input_handle in item.affected_inputs
        )
        if covered == (item.item_id in deferred):
            errors.append(
                f"{item.item_id} must be grouped or deferred exactly once."
            )
    return errors


def _build_task(
    *,
    group_id: str,
    shape: PatchGroupShape,
    by_item: dict[str, ActionableFailure],
) -> PatchGroupTask:
    items = [by_item[item_id] for item_id in shape.item_ids]
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
            if solution.input not in shape.inputs:
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
        item_ids=shape.item_ids,
        root_failure_refs=roots,
        inputs=shape.inputs,
        objective="; ".join(causes),
        requirements=requirements,
        candidate_hints=hints,
        interaction_notes=notes,
    )


def _response_json(response: LLMResponse) -> str:
    value = (
        response.parsed_json
        if response.parsed_json is not None
        else response.content
        if response.content is not None
        else {}
    )
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
