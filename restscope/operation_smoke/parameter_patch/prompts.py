"""Build the compact domain context for one Parameter Patch Agent session.

Failure Solve has already identified a root cause and selected affected semantic
inputs.  This module projects only those Generators, active Constraints,
reference aliases, and compatibility facts into safe text.  The common Context
Module later manages the growing propose/validation/accept conversation.
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
# Purpose
Convert one confirmed Failure requirement into the smallest complete Generator
and/or Constraint replacement. Do not diagnose a new cause or change target
inputs.

# Protocol
Use propose → runtime compile/sample → accept. Return strict
ParameterPatchDecision JSON only. ``action`` and ``patch`` are top-level
fields: a proposal is ``action="propose"`` plus one complete ``patch``; an
acceptance is only ``action="accept"`` with no ``patch``. Never wrap either
decision under a ``propose`` or ``accept`` property. ``patch`` contains changes
and constraints. A later proposal replaces the earlier proposal. Acceptance is
valid only after successful sample feedback. Never mix prose with the decision.

# Generator Signatures
constant | value
choice | values(non-empty); weights?(same length, non-negative, some positive)
integer_range | minimum:int; maximum:int; inclusive
number_range | minimum:number; maximum:number; inclusive
random_string | min_length:int; max_length:int; alphabet(non-empty when needed)
regex | pattern(Python search, <=2000 chars); min_length; max_length
boolean | true_probability:0..1
format | format:uuid|date|date-time|email
array | min_items:int; max_items:int
variant | branch_weights(non-empty, frozen branch count)
object | system-managed; never construct
request_body | system-managed; never construct
resource_identifier | system-selected through supplied R alias only
response_value | system-selected through supplied R alias only

Each change names one supplied semantic input and may set
inclusion_probability, strategy, or reference. strategy and reference are
mutually exclusive. A response-value or resource-identifier change places the
supplied R alias in ``reference`` beside ``input`` and omits ``strategy``.
Use only supplied R aliases; never emit raw input_node_id.

# Constraint Signatures
value: input_value(input) | literal(value) |
  arithmetic(operator:+|-|*|/, left:value, right:value)
boolean: present(input) |
  compare(operator:==|!=|<|<=|>|>=, left:value, right:value) |
  matches(value, pattern) |
  implies(condition:boolean, consequence:boolean) |
  cardinality(expressions:1..100, minimum, maximum) |
  and/or(expressions:1..100) | not(expression:boolean)
Each top-level constraint has exactly expression. Use at most 20. Ordered
comparisons and arithmetic require compatible numeric values; matches requires
a string-compatible value.

# Review
propose must cover only affected inputs and every stated requirement. The
runtime validates DTO shape, schema compatibility, references, Constraints,
and samples. Before accept, inspect every affected input, presence flag,
representative sample, range/type summary, supplied reference-pool value, and
Constraint result. If any requirement is not met, propose one complete
replacement. Do not call tools, send HTTP, persist state, or emit prose.
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
            "strategy": configs[node_id].strategy.model_dump(mode="python"),
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
    writer.record(
        task.todo_id,
        failure=task.failure,
        root_cause=task.root_cause,
        affected_inputs=task.affected_inputs,
    )
    writer.text("desired_behavior", task.desired_behavior)
    writer.text("acceptance_criteria", task.acceptance_criteria)

    writer.section("CURRENT GENERATORS", untrusted=True)
    for handle in task.affected_inputs:
        writer.record(handle, **current[handle])

    writer.section("ACTIVE CONSTRAINTS", untrusted=True)
    constraints = list(active_constraints or [])
    if not constraints:
        writer.record("none", count=0)
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
        writer.record("none", count=0)
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
    if not task.prior_attempts:
        writer.record("none", count=0)
    for index, attempt in enumerate(task.prior_attempts, start=1):
        writer.record(
            f"H{index}",
            required=False,
            **_bounded_history_fields(attempt),
        )
    rendered = writer.render(max_chars=18_000)
    return ParameterPatchPrompt(
        system=system_prompt or EXPERT_SYSTEM_PROMPT,
        user=rendered.text,
        reference_by_alias=references,
        metrics=rendered.metrics,
    )


def _bounded_history_fields(value: dict[str, Any]) -> dict[str, Any]:
    """Keep compatibility facts while dropping transcript-like historical noise."""
    preferred = (
        "outcome",
        "failure",
        "root_cause",
        "solution",
        "conflict_reason",
        "affected_inputs",
        "before_generators",
        "after_generators",
    )
    selected = {key: value[key] for key in preferred if key in value}
    if selected:
        return selected
    # Evaluation scenarios may use a concise custom compatibility record. The
    # writer still bounds and escapes every field.
    return dict(list(value.items())[:12])


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
