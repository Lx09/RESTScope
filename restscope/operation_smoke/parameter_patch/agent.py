"""Construct and locally review one Solve-owned parameter Patch.

Failure Solve decides why a batch failed and what behavior must change.
Parameter Patch converts that requirement into executable Generator and
Constraint objects. Free-form model choices are retained, while code enforces
only input scope, schemas, references, Constraint satisfiability, and local
generation before the same model may accept its latest complete proposal.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from restscope.context import AgentContext, CompactTextWriter, ContextLimits
from restscope.llm import (
    LLMClient,
    LLMModelConfig,
    LLMRequest,
    LLMResponse,
    OutputValidator,
)
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
    validate_constraint_set,
)
from restscope.testing.generation import generate_test_case

from .prompts import build_parameter_patch_prompt
from .schemas import (
    AvailableReferenceOption,
    CompiledConstraintPatch,
    GeneratorPatchDraft,
    ParameterPatchDecision,
    ParameterPatchFailure,
    ParameterPatchProposal,
    ParameterPatchTask,
    ValidatedParameterPatch,
)


_MAX_ERRORS = 20


class ParameterPatchAgent:
    """Propose, compile, sample, and self-review one Patch requirement."""

    def __init__(
        self,
        *,
        client: LLMClient,
        model: LLMModelConfig,
        system_prompt: str | None = None,
        validator: OutputValidator | None = None,
        tracing_runtime: TracingRuntime | None = None,
    ) -> None:
        """Store immutable model, prompt, validation, and tracing collaborators.

        The optional prompt is a complete evaluation replacement. Production
        callers leave it unset and keep the built-in expert instructions.
        """
        self.client = client
        self.model = model
        self.system_prompt = system_prompt
        self.validator = validator or OutputValidator()
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
        """Run a bounded propose → compile → sample → self-review conversation.

        Every model response consumes the output budget. Each proposal replaces
        the prior candidate, including when compilation fails. ``case_count``
        matches the surrounding Smoke request. ``random_seed`` is the one
        App-wide seed also used by Batch execution, so a maintainer can replay
        both the proposed values and the next full Batch.
        """
        if not 1 <= case_count <= 20:
            raise ValueError("case_count must be between 1 and 20")
        if not 1 <= max_outputs <= 20:
            raise ValueError("max_outputs must be between 1 and 20")
        if not self.model.enabled:
            raise RuntimeError("The Parameter Patch model is not configured")

        prompt = build_parameter_patch_prompt(
            task=task,
            config=config,
            reference_options=list(reference_options or []),
            model=self.model,
            active_constraints=active_constraints,
            system_prompt=self.system_prompt,
        )
        context = AgentContext(
            system=prompt.system,
            user=prompt.user,
            limits=ContextLimits(
                system_chars=7_000,
                initial_user_chars=18_000,
                feedback_chars=12_000,
                conversation_chars=36_000,
                required_output_tokens=self.model.max_tokens,
            ),
            metrics=prompt.metrics,
        )
        validated: tuple[
            GeneratorPatchDraft,
            list[dict[str, Any]],
            dict[str, list[Any]],
        ] | None = None
        latest_errors: list[str] = []
        attempt_history: list[dict[str, Any]] = []
        last_invalid_fingerprint: str | None = None
        repeated_invalid_count = 0

        with self.tracing_runtime.span(
            "ParameterPatchAgent.run",
            kind="AGENT",
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
            for name, value in context.metrics.trace_attributes().items():
                span.set_attribute(name, value)
            for output_number in range(1, max_outputs + 1):
                response = self.client.invoke(
                    LLMRequest(
                        provider=self.model.provider,
                        model=self.model.model,
                        messages=context.messages_for_request(self.model),
                        temperature=0,
                        max_tokens=self.model.max_tokens,
                        # The provider receives the authoritative decision
                        # schema as well as the human-readable prompt. DeepSeek
                        # currently enforces JSON syntax itself and appends this
                        # schema to its provider-owned instruction.
                        response_format="json_schema",
                        json_schema=ParameterPatchDecision.model_json_schema(),
                        json_schema_name="ParameterPatchDecision",
                        tools=[],
                        tool_choice="none",
                        timeout_seconds=self.model.timeout_seconds,
                        reasoning=self.model.reasoning,
                        metadata={"role": "parameter_patch_agent"},
                    )
                )
                attempt_history.append(
                    {
                        "content": response.content,
                        "parsed_json": response.parsed_json,
                        "finish_reason": response.finish_reason,
                    }
                )
                decision, errors = self._parse(response)
                if decision is not None and not errors:
                    if decision.action == "accept":
                        if validated is None:
                            errors = [
                                "accept requires compiled sample feedback"
                            ]
                        else:
                            patch, samples, pool_values = validated
                            outcome = ValidatedParameterPatch(
                                todo_id=task.todo_id,
                                patch=patch,
                                samples=samples,
                                outputs_used=output_number,
                                attempt_history=list(attempt_history),
                            )
                            for name, value in context.metrics.trace_attributes().items():
                                span.set_attribute(name, value)
                            span.set_output(
                                {
                                    "status": outcome.status,
                                    "outputs_used": outcome.outputs_used,
                                    "sample_count": len(samples),
                                    "reference_pool_count": sum(
                                        len(values)
                                        for values in pool_values.values()
                                    ),
                                }
                            )
                            return outcome
                    else:
                        assert decision.patch is not None
                        proposal = decision.patch
                        assert isinstance(proposal, ParameterPatchProposal)
                        # A new proposal invalidates prior sample acceptance even
                        # when this replacement cannot compile.
                        validated = None
                        try:
                            patch = _compile_patch(
                                proposal,
                                task=task,
                                config=config,
                                reference_by_alias=prompt.reference_by_alias,
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
                            pool_values = _reference_pool_values(
                                reference_by_alias=prompt.reference_by_alias,
                                reference_values=reference_values,
                            )
                            validated = (patch, samples, pool_values)
                        except (KeyError, TypeError, ValueError) as exc:
                            errors = [str(exc)]
                        else:
                            # A compiled and sampled candidate is genuine new
                            # progress. Clear any earlier invalid streak before
                            # asking the model to review the samples.
                            last_invalid_fingerprint = None
                            repeated_invalid_count = 0
                            context.append_assistant(response)
                            context.append_feedback(
                                _sample_feedback(
                                    task=task,
                                    samples=samples,
                                    pool_values=pool_values,
                                    active_constraint_count=len(active_constraints),
                                    patch_constraint_count=len(patch.constraints),
                                )
                            )
                            continue

                latest_errors = errors or ["The Patch output could not be used."]
                # DTO-valid proposals can still violate Solve's affected-input
                # boundary, reference pools, variant selection, or executable
                # sampling rules. Repeating the same rejected candidate with
                # the same validation result is no more useful than repeating
                # malformed JSON, so both share the three-strike guard.
                fingerprint = _invalid_output_fingerprint(
                    response,
                    errors=latest_errors,
                )
                if fingerprint == last_invalid_fingerprint:
                    repeated_invalid_count += 1
                else:
                    last_invalid_fingerprint = fingerprint
                    repeated_invalid_count = 1
                if repeated_invalid_count >= 3:
                    failure = ParameterPatchFailure(
                        todo_id=task.todo_id,
                        reason="repeated_invalid_output",
                        outputs_used=output_number,
                        errors=latest_errors[:_MAX_ERRORS],
                        attempt_history=list(attempt_history),
                    )
                    for name, value in context.metrics.trace_attributes().items():
                        span.set_attribute(name, value)
                    span.set_output(
                        {
                            "status": failure.status,
                            "reason": failure.reason,
                            "outputs_used": failure.outputs_used,
                            "error_count": len(failure.errors),
                        }
                    )
                    return failure
                context.append_assistant(response)
                context.append_feedback(_invalid_output_feedback(latest_errors))

            failure = ParameterPatchFailure(
                todo_id=task.todo_id,
                reason="output_budget_exhausted",
                outputs_used=max_outputs,
                errors=latest_errors[:_MAX_ERRORS],
                attempt_history=list(attempt_history),
            )
            for name, value in context.metrics.trace_attributes().items():
                span.set_attribute(name, value)
            span.set_output(
                {
                    "status": failure.status,
                    "reason": failure.reason,
                    "outputs_used": failure.outputs_used,
                    "error_count": len(failure.errors),
                }
            )
            return failure

    def _parse(
        self,
        response: LLMResponse,
    ) -> tuple[ParameterPatchDecision | None, list[str]]:
        """Parse one strict Patch proposal or acceptance."""
        result = self.validator.validate(
            response=response,
            output_model=ParameterPatchDecision,
        )
        if not result.valid:
            return None, [
                (
                    f"{issue.location}: {issue.message}"
                    if issue.location
                    else issue.message
                )
                for issue in result.errors[:_MAX_ERRORS]
            ]
        return (
            ParameterPatchDecision.model_validate(result.validated_object),
            [],
        )


def _invalid_output_feedback(errors: list[str]) -> str:
    """Explain a rejected decision without letting model text alter Markdown.

    Validation paths and messages may include fragments derived from the
    previous model output. Encoding them through ``CompactTextWriter`` keeps
    those fragments inside the evidence section while a separate trusted
    section states the exact repair contract.
    """

    writer = CompactTextWriter(max_value_chars=800)
    writer.section("PATCH OUTPUT INVALID", untrusted=True)
    for index, error in enumerate(errors[:_MAX_ERRORS], start=1):
        writer.text(f"issue {index}", error)
    writer.section("REQUIRED DECISION SHAPE")
    writer.text(
        "top level",
        "`action` and `patch` must be top-level fields.",
    )
    writer.text(
        "wrapper",
        "Do not wrap the decision under `propose` or `accept`.",
    )
    writer.text(
        "proposal",
        '`action` is "propose" and `patch` is the complete replacement.',
    )
    writer.text(
        "acceptance",
        '`action` is "accept" and `patch` is omitted.',
    )
    writer.text(
        "reference",
        "For an R alias, set `reference` beside `input` and omit `strategy`.",
    )
    writer.text(
        "next",
        "Return one complete corrected ParameterPatchDecision JSON object.",
    )
    return writer.render(max_chars=12_000).text


def _invalid_output_fingerprint(
    response: LLMResponse,
    *,
    errors: list[str],
) -> str:
    """Identify semantically identical rejected attempts across retries.

    Parsed JSON is serialized with stable key ordering so property order alone
    does not evade the guard. Validation errors are part of the identity so a
    candidate that reaches a genuinely different compiler or sampling result
    starts a new streak. If the provider could not parse JSON, raw content is
    used as the candidate value.
    """

    value = (
        response.parsed_json
        if response.parsed_json is not None
        else response.content
    )
    normalized = json.dumps(
        {"candidate": value, "errors": errors},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(normalized.encode()).hexdigest()


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
            index=index,
            task=task,
            semantic=semantic.node_by_handle,
            config=config,
        )
        for index, change in enumerate(proposal.constraints, start=1)
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
    index: int,
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
        f"{task.todo_id}:{index}:{encoded}".encode()
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
    """Generate request-shaped local samples without sending HTTP requests."""
    candidate = preview_generator_patch(config, patch.updates)
    expressions = [
        expression
        for item in [*active_constraints, *patch.constraints]
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


def _reference_pool_values(
    *,
    reference_by_alias: dict[str, AvailableReferenceOption],
    reference_values: ReferenceValueProvider | None,
) -> dict[str, list[Any]]:
    """Expose bounded raw pool values only to the Patch self-review."""
    if reference_values is None:
        return {}
    values: dict[str, list[Any]] = {}
    for alias, option in reference_by_alias.items():
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
        values[alias] = list(reference_values.values_for(strategy))[:100]
    return values


def _sample_feedback(
    *,
    task: ParameterPatchTask,
    samples: list[dict[str, Any]],
    pool_values: dict[str, list[Any]],
    active_constraint_count: int,
    patch_constraint_count: int,
) -> str:
    """Render exact affected values plus compact compatibility summaries.

    Samples remain request-shaped runtime objects internally. Only this typed
    table reaches the model, which makes absence distinct from null and avoids
    hiding a type change inside JSON punctuation.
    """
    writer = CompactTextWriter(max_value_chars=600)
    writer.section("VALIDATION PASSED")
    writer.record(
        "result",
        samples=len(samples),
        active_constraints=active_constraint_count,
        patch_constraints=patch_constraint_count,
        constraints_satisfied=True,
    )
    headers = ["sample"]
    for handle in task.affected_inputs:
        headers.extend((f"{handle}.present", f"{handle}.value"))
    rows: list[list[Any]] = []
    for index, sample in enumerate(samples, start=1):
        row: list[Any] = [f"S{index}"]
        values = sample["values"]
        present = sample["present"]
        for handle in task.affected_inputs:
            is_present = bool(present[handle])
            row.extend(
                (
                    is_present,
                    values[handle]
                    if is_present
                    else CompactTextWriter.ABSENT,
                )
            )
        rows.append(row)
    writer.table(headers, rows)

    writer.section("SAMPLE SUMMARY")
    for handle in task.affected_inputs:
        observed = [
            sample["values"][handle]
            for sample in samples
            if sample["present"][handle]
        ]
        writer.record(
            handle,
            present_count=len(observed),
            absent_count=len(samples) - len(observed),
            types=sorted({type(value).__name__ for value in observed}),
            minimum=_numeric_boundary(observed, minimum=True),
            maximum=_numeric_boundary(observed, minimum=False),
        )

    writer.section("REFERENCE POOLS", untrusted=True)
    if not pool_values:
        writer.record("none", count=0)
    for alias, values in pool_values.items():
        writer.detail(alias, {"values": values})
    writer.text(
        "next",
        "Return action=accept, or propose one complete replacement.",
    )
    return writer.render(max_chars=12_000).text


def _numeric_boundary(values: list[Any], *, minimum: bool) -> Any:
    """Return a numeric range endpoint without treating booleans as integers."""
    numbers = [
        value
        for value in values
        if isinstance(value, (int, float)) and not isinstance(value, bool)
    ]
    if not numbers:
        return CompactTextWriter.ABSENT
    return min(numbers) if minimum else max(numbers)
