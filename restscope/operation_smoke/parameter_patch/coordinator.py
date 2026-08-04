"""Coordinate proposal, deterministic validation, and independent review.

Failure Solve decides why a batch failed and what behavior must change.
Parameter Patch converts that requirement into executable Generator and
Constraint objects. This deterministic Module owns budgets and feedback while
the Patch Agent proposes and a fresh Review Agent judges semantic alignment.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from restscope.context import CompactTextWriter
from restscope.llm import LLMClient, LLMModelConfig
from restscope.observability import TracingRuntime
from restscope.testing import (
    ConstraintSet,
    InputGeneratorPatch,
    OperationGeneratorConfig,
    ReferenceValueProvider,
    ResourceIdentifierGenerator,
    ResponseValueGenerator,
    assignments_from_generated_case,
    build_semantic_input_map,
    classify_constraint,
    normalize_constraint_set,
    preview_generator_patch,
    project_generated_input_value,
    referenced_input_node_ids,
    validate_constraint_set,
)
from restscope.testing.generation import generate_test_case

from .agent import ParameterPatchAgent, ParameterPatchAttempt
from .prompts import build_parameter_patch_prompt
from .review import (
    ParameterPatchReviewAgent,
    ParameterPatchReviewCandidate,
    ParameterPatchReviewFailure,
)
from .schemas import (
    AvailableReferenceOption,
    CompiledConstraintPatch,
    GeneratorPatchDraft,
    ParameterPatchFailure,
    ParameterPatchProposal,
    ParameterPatchTask,
    ValidatedParameterPatch,
)


_MAX_ERRORS = 20


class ParameterPatchCoordinator:
    """Own proposal validation, independent review, feedback, and output budget."""

    def __init__(
        self,
        *,
        client: LLMClient,
        patch_model: LLMModelConfig,
        review_model: LLMModelConfig,
        patch_system_prompt: str | None = None,
        review_system_prompt: str | None = None,
        tracing_runtime: TracingRuntime | None = None,
    ) -> None:
        """Store collaborators for one short-lived Patch coordination run."""
        self.client = client
        self.patch_model = patch_model
        self.review_model = review_model
        self.patch_system_prompt = patch_system_prompt
        self.review_system_prompt = review_system_prompt
        self.tracing_runtime = tracing_runtime or TracingRuntime.disabled()

    def run(
        self,
        *,
        task: ParameterPatchTask,
        config: OperationGeneratorConfig,
        active_constraints: list[CompiledConstraintPatch],
        case_count: int,
        reference_values: ReferenceValueProvider | None = None,
        reference_options: list[AvailableReferenceOption] | None = None,
        random_seed: int = 0,
        max_outputs: int = 20,
    ) -> ValidatedParameterPatch | ParameterPatchFailure:
        """Coordinate proposal, local validation, and fresh semantic review.

        Both Agents spend the same output budget. Compile failures consume only
        the proposal; successful candidates always consume a fresh Reviewer
        output before they can become ``ValidatedParameterPatch``.
        """
        if not 1 <= case_count <= 20:
            raise ValueError("case_count must be between 1 and 20")
        if not 1 <= max_outputs <= 20:
            raise ValueError("max_outputs must be between 1 and 20")
        if not self.patch_model.enabled or not self.review_model.enabled:
            raise RuntimeError("Both Parameter Patch models must be configured")

        prompt = build_parameter_patch_prompt(
            task=task,
            config=config,
            reference_options=list(reference_options or []),
            model=self.patch_model,
            active_constraints=active_constraints,
            system_prompt=self.patch_system_prompt,
        )
        patch_agent = ParameterPatchAgent(
            client=self.client,
            model=self.patch_model,
            prompt=prompt,
            tracing_runtime=self.tracing_runtime,
        )
        outputs_used = 0
        latest_errors: list[str] = []
        attempt_history: list[dict[str, Any]] = []
        last_invalid_fingerprint: str | None = None
        repeated_invalid_count = 0

        with self.tracing_runtime.span(
            "ParameterPatchCoordinator.run",
            kind="INTERNAL",
            input_value={
                "todo_id": task.todo_id,
                "input_count": len(task.affected_inputs),
                "case_count": case_count,
                "max_outputs": max_outputs,
            },
            attributes={
                "restscope.patch.todo_id": task.todo_id,
                "restscope.patch.input_count": len(task.affected_inputs),
                "restscope.patch.sample_count": case_count,
            },
        ) as span:
            while outputs_used < max_outputs:
                attempt = patch_agent.propose(
                    shared_output_number=outputs_used + 1,
                )
                outputs_used += 1
                attempt_history.append(_patch_attempt_record(attempt))
                errors = list(attempt.errors)
                patch: GeneratorPatchDraft | None = None
                samples: list[dict[str, Any]] = []

                if attempt.submission is not None and not errors:
                    proposal = attempt.submission.patch
                    try:
                        patch = _compile_patch(
                            proposal,
                            task=task,
                            config=config,
                            reference_by_alias=patch_agent.reference_by_alias,
                        )
                        samples = _sample_patch(
                            task=task,
                            config=config,
                            patch=patch,
                            active_constraints=active_constraints,
                            reference_values=reference_values,
                            case_count=case_count,
                            random_seed=random_seed,
                        )
                    except (KeyError, TypeError, ValueError) as exc:
                        errors = [str(exc)]

                if patch is not None and not errors:
                    if outputs_used >= max_outputs:
                        latest_errors = [
                            "No shared output budget remains for independent review."
                        ]
                        break
                    review_candidate = _build_review_candidate(
                        task=task,
                        config=config,
                        active_constraints=active_constraints,
                        proposal=attempt.submission.patch,
                        patch=patch,
                        samples=samples,
                    )
                    reviewer = ParameterPatchReviewAgent(
                        client=self.client,
                        model=self.review_model,
                        system_prompt=self.review_system_prompt,
                        tracing_runtime=self.tracing_runtime,
                    )
                    review = reviewer.run(
                        review_candidate,
                        max_outputs=max_outputs - outputs_used,
                        shared_outputs_used=outputs_used,
                    )
                    outputs_used += review.outputs_used
                    attempt_history.extend(
                        {"agent": "parameter_patch_review_agent", **item}
                        for item in review.attempt_history
                    )
                    span.set_attribute(
                        "restscope.patch.shared_outputs_used", outputs_used
                    )
                    if isinstance(review, ParameterPatchReviewFailure):
                        failure = ParameterPatchFailure(
                            todo_id=task.todo_id,
                            reason=review.reason,
                            outputs_used=outputs_used,
                            errors=review.errors,
                            attempt_history=attempt_history,
                        )
                        span.set_output(
                            {
                                "status": failure.status,
                                "reason": failure.reason,
                                "outputs_used": outputs_used,
                                "review_issue_count": 0,
                            }
                        )
                        return failure
                    if review.accepted:
                        outcome = ValidatedParameterPatch(
                            todo_id=task.todo_id,
                            patch=patch,
                            samples=samples,
                            outputs_used=outputs_used,
                            attempt_history=attempt_history,
                        )
                        span.set_output(
                            {
                                "status": outcome.status,
                                "outputs_used": outputs_used,
                                "sample_count": len(samples),
                                "review_issue_count": 0,
                                "reference_count": len(
                                    patch.selected_reference_options
                                ),
                            }
                        )
                        return outcome
                    errors = list(review.issues)

                latest_errors = errors or ["The Patch proposal could not be used."]
                fingerprint = _invalid_attempt_fingerprint(attempt, latest_errors)
                if fingerprint == last_invalid_fingerprint:
                    repeated_invalid_count += 1
                else:
                    last_invalid_fingerprint = fingerprint
                    repeated_invalid_count = 1
                if repeated_invalid_count >= 3:
                    failure = ParameterPatchFailure(
                        todo_id=task.todo_id,
                        reason="repeated_invalid_output",
                        outputs_used=outputs_used,
                        errors=latest_errors[:_MAX_ERRORS],
                        attempt_history=attempt_history,
                    )
                    span.set_output(
                        {
                            "status": failure.status,
                            "reason": failure.reason,
                            "outputs_used": outputs_used,
                            "review_issue_count": len(latest_errors),
                        }
                    )
                    return failure
                # Compiler errors and semantic Review issues both refer to the
                # exact proposal call. Returning them as its matching result
                # preserves the Patch Agent's one continuous revision context.
                patch_agent.append_feedback(
                    attempt,
                    _proposal_feedback(latest_errors),
                )

            failure = ParameterPatchFailure(
                todo_id=task.todo_id,
                reason="output_budget_exhausted",
                outputs_used=outputs_used,
                errors=latest_errors[:_MAX_ERRORS],
                attempt_history=attempt_history,
            )
            span.set_output(
                {
                    "status": failure.status,
                    "reason": failure.reason,
                    "outputs_used": outputs_used,
                    "error_count": len(failure.errors),
                }
            )
            return failure


def _patch_attempt_record(attempt: ParameterPatchAttempt) -> dict[str, Any]:
    """Keep bounded transport diagnostics without provider reasoning."""
    return {
        "agent": "parameter_patch_agent",
        "content": attempt.response.content,
        "parsed_json": attempt.response.parsed_json,
        "tool_calls": [
            {"name": call.name, "arguments": call.arguments}
            for call in attempt.response.tool_calls
        ],
        "finish_reason": attempt.response.finish_reason,
        "transport": attempt.transport,
    }


def _proposal_feedback(errors: list[str]) -> str:
    """Render compiler or Reviewer issues as bounded untrusted feedback."""
    writer = CompactTextWriter(max_value_chars=800)
    writer.section("PATCH PROPOSAL REJECTED", untrusted=True)
    for index, error in enumerate(errors[:_MAX_ERRORS], start=1):
        writer.text(f"issue {index}", error)
    writer.section("REQUIRED PROPOSAL SHAPE")
    writer.text("action", 'Use action="propose".')
    writer.text("patch", "Submit one complete replacement patch.")
    writer.text(
        "patch keys",
        "changes and constraints are the only patch keys.",
    )
    writer.text(
        "forbidden keys",
        "Never use generators, generator_changes, and constraint_changes.",
    )
    # DeepSeek's strict transport currently accepts calls whose nested fields
    # still violate the recursive schema. State the two observed corrections
    # only after a rejected proposal; putting the recursive contract into the
    # initial system prompt caused the provider to return empty arguments.
    writer.text(
        "change input",
        'Each change uses "input", never "input_handle".',
    )
    writer.text(
        "constraint expression",
        (
            "Each constraint expression must be a recursive object, never a "
            "string expression."
        ),
    )
    writer.text(
        "reference",
        "For an R alias, set reference beside input and omit strategy.",
    )
    return writer.render(max_chars=12_000).text


def _invalid_attempt_fingerprint(
    attempt: ParameterPatchAttempt,
    errors: list[str],
) -> str:
    """Identify the same proposal receiving the same rejection three times."""
    response = attempt.response
    value: Any = (
        [
            {"name": call.name, "arguments": call.arguments}
            for call in response.tool_calls
        ]
        or response.parsed_json
        or response.content
    )
    normalized = json.dumps(
        {"candidate": value, "errors": errors},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(normalized.encode()).hexdigest()


def _build_review_candidate(
    *,
    task: ParameterPatchTask,
    config: OperationGeneratorConfig,
    active_constraints: list[CompiledConstraintPatch],
    proposal: ParameterPatchProposal,
    patch: GeneratorPatchDraft,
    samples: list[dict[str, Any]],
) -> ParameterPatchReviewCandidate:
    """Build a fresh Reviewer view without Patch dialogue or error history.

    The Reviewer receives semantic handles and normalized final facts only.
    Runtime node identifiers and the Patch Agent's prior messages remain inside
    deterministic code, where they cannot bias the independent judgment.
    """
    semantic = build_semantic_input_map(config)
    candidate_config = preview_generator_patch(config, patch.updates)
    return ParameterPatchReviewCandidate(
        requirement={
            "failure": task.failure,
            "root_cause": task.root_cause,
            "desired_behavior": task.desired_behavior,
            "acceptance_criteria": task.acceptance_criteria,
        },
        affected_inputs=list(task.affected_inputs),
        before_generators=_generator_summary(config, task.affected_inputs),
        after_generators=_generator_summary(
            candidate_config,
            task.affected_inputs,
        ),
        proposal=proposal.model_dump(mode="python"),
        reference_provenance=[
            {
                "input": semantic.handle_by_node[option.input_node_id],
                "kind": option.kind,
                "resource": option.canonical_resource,
                "value_name": option.value_name,
                "producer_operations": option.producer_operation_keys,
                "status": option.producer_status_code,
                "media_type": option.producer_media_type,
                "field": option.source_field,
                "selector": option.source_selector,
            }
            for option in patch.selected_reference_options
        ],
        active_constraints=[
            _constraint_summary(item, semantic.handle_by_node)
            for item in active_constraints
        ],
        candidate_constraints=[
            _constraint_summary(item, semantic.handle_by_node)
            for item in patch.constraints
        ],
        samples=samples,
    )


def _generator_summary(
    config: OperationGeneratorConfig,
    affected_inputs: list[str],
) -> dict[str, Any]:
    """Describe selected Generators with semantic handles for Reviewer use."""
    semantic = build_semantic_input_map(config)
    by_node = {item.input_node_id: item for item in config.configs}
    return {
        handle: {
            "inclusion_probability": by_node[
                semantic.node_by_handle[handle]
            ].inclusion_probability,
            "strategy": by_node[
                semantic.node_by_handle[handle]
            ].strategy.model_dump(mode="python"),
        }
        for handle in affected_inputs
    }


def _constraint_summary(
    constraint: CompiledConstraintPatch,
    handle_by_node: Mapping[str, str],
) -> dict[str, Any]:
    """Convert one executable Constraint back to semantic review facts."""
    return {
        "kind": constraint.kind,
        "expression": _semantic_constraint(
            constraint.constraint.model_dump(mode="python"),
            handle_by_node,
        ),
    }


def _semantic_constraint(value: Any, handle_by_node: Mapping[str, str]) -> Any:
    """Recursively replace runtime input IDs with model-facing handles."""
    if isinstance(value, list):
        return [_semantic_constraint(item, handle_by_node) for item in value]
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


def _compile_patch(
    proposal: ParameterPatchProposal,
    *,
    task: ParameterPatchTask,
    config: OperationGeneratorConfig,
    reference_by_alias: dict[str, AvailableReferenceOption],
) -> GeneratorPatchDraft:
    """Translate semantic model output into executable testing objects."""
    semantic = build_semantic_input_map(config)
    allowed = set(task.affected_inputs)
    supplied = [change.input for change in proposal.changes]
    if len(supplied) != len(set(supplied)):
        raise ValueError("each semantic input may be changed at most once")
    updates: list[InputGeneratorPatch] = []
    selected_options: list[AvailableReferenceOption] = []
    for change in proposal.changes:
        if change.input not in allowed:
            raise ValueError(
                f"{change.input} is outside the Solve Patch requirement"
            )
        node_id = semantic.node_by_handle.get(change.input)
        if node_id is None:
            raise ValueError(f"Unknown semantic input: {change.input}")
        strategy = change.strategy
        if strategy is not None and strategy.type in {"object", "request_body"}:
            raise ValueError(
                f"{strategy.type} is system-managed and cannot be patched"
            )
        if strategy is not None and strategy.type in {
            "resource_identifier",
            "response_value",
        }:
            raise ValueError(
                "Observed generators must use a system-provided R alias"
            )
        if change.reference is not None:
            option = reference_by_alias.get(change.reference)
            if option is None:
                raise ValueError(
                    f"Unknown observed-value source: {change.reference}"
                )
            if option.input_node_id != node_id:
                raise ValueError(
                    f"{change.reference} is not available for {change.input}"
                )
            strategy = (
                ResourceIdentifierGenerator(
                    type="resource_identifier",
                    resource=option.canonical_resource,
                )
                if option.kind == "resource_identifier"
                else ResponseValueGenerator(
                    type="response_value",
                    value_name=option.value_name,
                )
            )
            selected_options.append(option)
        updates.append(
            InputGeneratorPatch(
                input_node_id=node_id,
                inclusion_probability=change.inclusion_probability,
                strategy=strategy,
            )
        )
    _validate_variant_branch_updates(
        config=config,
        updates=updates,
        handle_by_node=semantic.handle_by_node,
    )
    constraints = [
        _compile_constraint(
            change.expression,
            task=task,
            semantic=semantic.node_by_handle,
            config=config,
        )
        for change in proposal.constraints
    ]
    patch = GeneratorPatchDraft(
        updates=updates,
        constraints=constraints,
        selected_reference_options=selected_options,
    )
    preview_generator_patch(config, patch.updates)
    return patch


def _validate_variant_branch_updates(
    *,
    config: OperationGeneratorConfig,
    updates: list[InputGeneratorPatch],
    handle_by_node: Mapping[str, str],
) -> None:
    """Require a deterministic parent selection for every changed branch.

    Updating only a child below ``oneOf`` or ``anyOf`` leaves the parent
    Variant free to choose another branch. Such a candidate can look correct
    in some samples while remaining unable to guarantee the requested repair.
    The model must therefore patch every enclosing Variant with exclusive
    weights that select the changed child's branch.

    Args:
        config: Frozen operation inputs and their current Generators.
        updates: Candidate Generator changes compiled from semantic handles.
        handle_by_node: Model-facing names used in actionable error messages.

    Raises:
        ValueError: A changed branch has no explicit exclusive parent Variant
            selection. No Generator state is changed by this validation.
    """
    nodes = {
        item.input_node_id: item
        for item in config.snapshot.input_nodes
    }
    current_configs = {
        item.input_node_id: item
        for item in config.configs
    }
    updates_by_node = {
        item.input_node_id: item
        for item in updates
    }

    for changed_node_id in updates_by_node:
        branch_node_id = changed_node_id
        current = nodes[changed_node_id]
        while current.parent_node_id is not None:
            parent = nodes[current.parent_node_id]
            parent_config = current_configs[parent.input_node_id]
            if parent_config.strategy.type == "variant":
                parent_update = updates_by_node.get(parent.input_node_id)
                parent_strategy = (
                    parent_update.strategy
                    if parent_update is not None
                    else None
                )
                parent_handle = handle_by_node.get(
                    parent.input_node_id,
                    parent.canonical_path,
                )
                if (
                    parent_strategy is None
                    or parent_strategy.type != "variant"
                ):
                    raise ValueError(
                        f"{parent_handle} must select the changed variant "
                        "branch explicitly"
                    )

                branch_children = sorted(
                    (
                        node
                        for node in nodes.values()
                        if node.parent_node_id == parent.input_node_id
                        and (
                            "/oneOf/" in node.canonical_path
                            or "/anyOf/" in node.canonical_path
                        )
                    ),
                    key=_variant_branch_index,
                )
                selected_index = next(
                    (
                        index
                        for index, node in enumerate(branch_children)
                        if node.input_node_id == branch_node_id
                    ),
                    None,
                )
                weights = parent_strategy.branch_weights
                if (
                    selected_index is None
                    or selected_index >= len(weights)
                    or weights[selected_index] <= 0
                    or any(
                        weight > 0
                        for index, weight in enumerate(weights)
                        if index != selected_index
                    )
                ):
                    raise ValueError(
                        f"{parent_handle} branch_weights must exclusively "
                        "select the changed branch"
                    )
            branch_node_id = parent.input_node_id
            current = parent


def _variant_branch_index(node: Any) -> int:
    """Return the numeric OpenAPI branch position used by Variant weights.

    Args:
        node: One frozen input node whose canonical path ends in a branch
            index.

    Returns:
        The integer branch position, or zero for an unexpected non-numeric
        suffix so later structural validation can reject a mismatch safely.
    """
    tail = node.canonical_path.rstrip("/").rsplit("/", 1)[-1]
    try:
        return int(tail)
    except ValueError:
        return 0


def _compile_constraint(
    expression: dict[str, Any],
    *,
    task: ParameterPatchTask,
    semantic: Mapping[str, str],
    config: OperationGeneratorConfig,
) -> CompiledConstraintPatch:
    """Compile one semantic Constraint and reject out-of-scope inputs."""
    allowed = set(task.affected_inputs)

    def convert(value: Any) -> Any:
        """Replace semantic input handles recursively with frozen node IDs."""
        if isinstance(value, list):
            return [convert(item) for item in value]
        if not isinstance(value, dict):
            return value
        if "input_node_id" in value:
            raise ValueError("Constraint expressions must use semantic inputs")
        output = {key: convert(item) for key, item in value.items()}
        if value.get("type") in {"present", "input_value"}:
            handle = value.get("input")
            if not isinstance(handle, str) or handle not in semantic:
                raise ValueError(f"Unknown constraint input: {handle}")
            if handle not in allowed:
                raise ValueError(
                    f"{handle} is outside the Solve Patch requirement"
                )
            output.pop("input", None)
            output["input_node_id"] = semantic[handle]
        return output

    compiled = ConstraintSet.model_validate(
        {"constraints": [convert(expression)]}
    )
    validate_constraint_set(compiled, config.snapshot)
    normalized = normalize_constraint_set(compiled)
    encoded = json.dumps(
        normalized.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
    )
    identity = hashlib.sha256(
        f"{config.operation_key}:{encoded}".encode()
    ).hexdigest()[:16]
    return CompiledConstraintPatch(
        constraint_id=f"constraint_{identity}",
        kind=classify_constraint(normalized.constraints[0]),
        constraint=normalized,
    )


def _sample_patch(
    *,
    task: ParameterPatchTask,
    config: OperationGeneratorConfig,
    patch: GeneratorPatchDraft,
    active_constraints: list[CompiledConstraintPatch],
    reference_values: ReferenceValueProvider | None,
    case_count: int,
    random_seed: int,
) -> list[dict[str, Any]]:
    """Generate samples using the complete post-replacement Constraint set."""
    candidate = preview_generator_patch(config, patch.updates)
    final_constraints = _replace_candidate_constraint_scope(
        active_constraints,
        patch.constraints,
    )
    expressions = [
        expression
        for item in final_constraints
        for expression in item.constraint.constraints
    ]
    constraints = ConstraintSet(constraints=expressions) if expressions else None
    semantic = build_semantic_input_map(candidate)
    samples: list[dict[str, Any]] = []
    for case_index in range(case_count):
        generated = generate_test_case(
            candidate.snapshot,
            candidate,
            run_seed=random_seed,
            case_index=case_index,
            reference_values=reference_values,
            constraints=constraints,
        )
        assignments = assignments_from_generated_case(
            candidate.snapshot,
            generated,
        )
        values: dict[str, Any] = {}
        present: dict[str, bool] = {}
        for handle in task.affected_inputs:
            node_id = semantic.node_by_handle[handle]
            assignment = assignments[node_id]
            present[handle] = assignment.present
            values[handle] = (
                project_generated_input_value(
                    candidate.snapshot,
                    generated,
                    input_node_id=node_id,
                )
                if assignment.present
                else None
            )
        samples.append({"values": values, "present": present})
    return samples


def _replace_candidate_constraint_scope(
    current: list[CompiledConstraintPatch],
    replacement: list[CompiledConstraintPatch],
) -> list[CompiledConstraintPatch]:
    """Preview the same transitive owner replacement used at persistence.

    A Generator-only Patch carries no replacement Constraints and therefore
    preserves every current expression.  When expressions are present, their
    referenced input IDs seed an overlap frontier.  Any old Constraint whose
    owner intersects that frontier is replaced, including transitively linked
    old owners.
    """

    if not replacement:
        return list(current)

    def owners(item: CompiledConstraintPatch) -> set[str]:
        """Return input IDs referenced by one normalized expression set."""
        return set(referenced_input_node_ids(item.constraint))

    frontier = {
        node_id
        for item in replacement
        for node_id in owners(item)
    }
    replaced_ids: set[str] = set()
    changed = True
    while changed:
        changed = False
        for item in current:
            if item.constraint_id in replaced_ids:
                continue
            item_owners = owners(item)
            if item_owners & frontier:
                replaced_ids.add(item.constraint_id)
                frontier.update(item_owners)
                changed = True
    retained = [
        item for item in current if item.constraint_id not in replaced_ids
    ]
    by_id = {
        item.constraint_id: item for item in [*retained, *replacement]
    }
    return [by_id[key] for key in sorted(by_id)]
