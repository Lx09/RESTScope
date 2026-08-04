"""Build the compact domain context for one Parameter Patch Agent session.

Failure Solve has already identified a root cause and selected affected semantic
inputs.  This module projects only those Generators, active Constraints,
reference aliases, and compatibility facts into safe text. The common Context
Module later manages the growing proposal/revision conversation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from restscope.context import CompactTextWriter, ContextMetrics
from restscope.llm import LLMModelConfig
from restscope.testing import OperationGeneratorConfig, build_semantic_input_map

from .schemas import (
    AvailableReferenceOption,
    CompiledConstraintPatch,
    ParameterPatchTask,
)


EXPERT_SYSTEM_PROMPT = """
Convert the supplied requirement into the smallest complete Parameter Patch
proposal. Return one JSON object matching the supplied response Schema and emit
no prose. Change only affected inputs. Put Generator edits in patch.changes and
request relationships in patch.constraints. Each change may set
inclusion_probability, strategy, or an R alias reference; strategy and
reference are mutually exclusive. Use only supplied semantic input handles and
aliases, and preserve compatible existing behavior. When compiler or Reviewer
feedback follows, submit one complete corrected replacement.
""".strip()


@dataclass(slots=True, frozen=True)
class ParameterPatchPrompt:
    """Carry one compact prompt plus runtime-only reference alias mapping."""

    system: str
    user: str
    reference_by_alias: dict[str, AvailableReferenceOption]
    metrics: ContextMetrics


def build_parameter_patch_prompt(
    *,
    task: ParameterPatchTask,
    config: OperationGeneratorConfig,
    reference_options: list[AvailableReferenceOption],
    model: LLMModelConfig,
    active_constraints: list[CompiledConstraintPatch] | None = None,
    system_prompt: str | None = None,
) -> ParameterPatchPrompt:
    """Build the bounded initial task without serializing DTOs as JSON.

    Args:
        task: Solve-owned root cause, affected inputs, and acceptance contract.
        config: Current operation Generator configuration.
        reference_options: Populated observed-value sources available now.
        model: Model configuration retained for a stable builder interface; the
            shared AgentContext applies its actual window later.
        active_constraints: Current operation relationships that a replacement
            must continue to satisfy.
        system_prompt: Complete evaluation-only instruction replacement.

    Returns:
        Compact prompt text plus an alias map used only by deterministic Patch
        compilation.
    """
    del model  # Window fitting belongs to AgentContext, not this domain adapter.
    semantic = build_semantic_input_map(config)
    configs = {item.input_node_id: item for item in config.configs}
    current = {
        handle: {
            "inclusion_probability": configs[node_id].inclusion_probability,
            "strategy": _generator_strategy_summary(configs[node_id].strategy),
        }
        for handle, node_id in semantic.node_by_handle.items()
        if handle in task.affected_inputs
    }
    target_node_ids = {
        semantic.node_by_handle[handle]
        for handle in task.affected_inputs
    }
    references = {
        f"R{index}": option
        for index, option in enumerate(
            [
                option
                for option in reference_options
                if option.input_node_id in target_node_ids
            ],
            start=1,
        )
    }

    writer = CompactTextWriter(max_value_chars=800)
    writer.section("PATCH REQUIREMENT")
    writer.text("task", task.todo_id)
    writer.text("failure", task.failure)
    writer.text("root cause", task.root_cause)
    writer.detail("affected inputs", task.affected_inputs)
    writer.text("desired behavior", task.desired_behavior)
    writer.text("acceptance criteria", task.acceptance_criteria)

    writer.section("CURRENT GENERATORS", untrusted=True)
    for handle in task.affected_inputs:
        writer.record(handle, **current[handle])

    writer.section("ACTIVE CONSTRAINTS", untrusted=True)
    constraints = list(active_constraints or [])
    if not constraints:
        writer.text("constraints", "No active Constraints.")
    for constraint in constraints:
        writer.record(
            constraint.constraint_id,
            kind=constraint.kind,
            expression=_semantic_constraint(
                constraint.constraint.model_dump(mode="python"),
                semantic.handle_by_node,
            ),
        )

    writer.section("REFERENCE ALIASES", untrusted=True)
    if not references:
        writer.text("references", "No populated reference aliases.")
    for alias, option in references.items():
        writer.record(
            alias,
            input=semantic.handle_by_node[option.input_node_id],
            kind=option.kind,
            value_count=option.value_count,
            resource=option.canonical_resource,
            value_name=option.value_name,
            producers=option.producer_operation_keys,
            status=option.producer_status_code,
            media=option.producer_media_type,
            field=option.source_field,
            selector=option.source_selector,
        )

    writer.section("PRIOR COMPATIBILITY", untrusted=True)
    history = _relevant_history(
        task.prior_attempts,
        affected_inputs=set(task.affected_inputs),
    )
    if not history:
        writer.text(
            "history",
            "No relevant applied or conflicting Patch history.",
        )
    for index, attempt in enumerate(history, start=1):
        writer.record(
            f"H{index}",
            required=False,
            **attempt,
        )
    rendered = writer.render(max_chars=18_000)
    return ParameterPatchPrompt(
        system=system_prompt or EXPERT_SYSTEM_PROMPT,
        user=rendered.text,
        reference_by_alias=references,
        metrics=rendered.metrics,
    )


def _generator_strategy_summary(strategy: Any) -> dict[str, Any]:
    """Show one Generator without null fields or an unbounded choice line.

    Choice Generators can contain hundreds of OpenAPI enum values. A short
    choice remains a normal array; a longer choice shows its exact size plus a
    stable head/tail preview selected by this workflow.
    """
    summary = strategy.model_dump(mode="python", exclude_none=True)
    if not summary.get("weights"):
        summary.pop("weights", None)
    values = summary.get("values")
    if isinstance(values, list) and len(values) > 20:
        summary["value_count"] = len(values)
        summary["values"] = {
            "first": values[:16],
            "omitted_count": len(values) - 20,
            "last": values[-4:],
        }
    return summary


def _relevant_history(
    histories: list[dict[str, Any]],
    *,
    affected_inputs: set[str],
) -> list[dict[str, Any]]:
    """Keep only real applied/conflict facts for the current Patch inputs.

    Parameter Memory may return an empty entry for every requested input. Those
    entries prove only that no history exists, so rendering one `H*` card per
    input adds noise. Internal database identities and event identities are
    also intentionally excluded from the model-facing projection.
    """
    selected: list[dict[str, Any]] = []
    for history in histories:
        input_handle = history.get("input_handle")
        if input_handle not in affected_inputs:
            continue
        failures: list[dict[str, Any]] = []
        for failure in history.get("failures") or []:
            attempts = [
                _history_attempt_summary(attempt)
                for attempt in failure.get("attempts") or []
                if attempt.get("outcome") in {"applied_patch", "conflict"}
            ]
            if not attempts:
                continue
            failures.append(
                {
                    "failure": failure.get("summary"),
                    "attempts": attempts,
                }
            )
        if failures:
            selected.append(
                {
                    "input": input_handle,
                    "failures": failures,
                }
            )
    return selected


def _history_attempt_summary(attempt: dict[str, Any]) -> dict[str, Any]:
    """Remove storage identities while retaining compatibility evidence."""
    summary = {
        key: attempt[key]
        for key in ("round_number", "outcome", "root_cause", "reason")
        if key in attempt and attempt[key] is not None
    }
    parameters = [
        parameter.get("input_handle")
        for parameter in attempt.get("parameters") or []
        if parameter.get("input_handle")
    ]
    if parameters:
        summary["affected_inputs"] = parameters
    change = attempt.get("generator_change")
    if change:
        if change.get("generator_changes"):
            summary["generator_changes"] = _without_internal_ids(
                change["generator_changes"]
            )
        if change.get("constraint_changes"):
            summary["constraint_changes"] = _without_internal_ids(
                change["constraint_changes"]
            )
    return summary


def _without_internal_ids(value: Any) -> Any:
    """Recursively remove persistence and runtime identities from history."""
    if isinstance(value, list):
        return [_without_internal_ids(item) for item in value]
    if not isinstance(value, dict):
        return value
    hidden = {
        "constraint_id",
        "event_id",
        "failure_id",
        "input_node_id",
        "solve_attempt_id",
    }
    return {
        key: _without_internal_ids(item)
        for key, item in value.items()
        if key not in hidden
    }


def _semantic_constraint(value: Any, handle_by_node) -> Any:
    """Replace runtime node IDs with the semantic handles shown to the model."""
    if isinstance(value, list):
        return [
            _semantic_constraint(item, handle_by_node)
            for item in value
        ]
    if not isinstance(value, dict):
        return value
    output = {
        key: _semantic_constraint(item, handle_by_node)
        for key, item in value.items()
        if key != "input_node_id"
    }
    if "input_node_id" in value:
        output["input"] = handle_by_node.get(
            value["input_node_id"],
            "<inactive-input>",
        )
    return output
