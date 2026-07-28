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
from typing import Any

from restscope.agent.prompt_context import fit_message_context
from restscope.llm import (
    LLMClient,
    LLMMessage,
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
        validator: OutputValidator | None = None,
        tracing_runtime: TracingRuntime | None = None,
    ) -> None:
        """Store immutable model, validation, and tracing collaborators."""
        self.client = client
        self.model = model
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
        max_outputs: int = 20,
    ) -> ValidatedParameterPatch | ParameterPatchFailure:
        """Run a bounded propose → compile → sample → self-review conversation.

        Every model response consumes the output budget. Each proposal replaces
        the prior candidate, including when compilation fails. ``case_count``
        matches the surrounding Smoke request so local review scales from one
        to twenty samples instead of relying on a fixed ten-sample rule.
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
        )
        messages = [
            LLMMessage(role="system", content=prompt.system),
            LLMMessage(role="user", content=prompt.user),
        ]
        validated: tuple[
            GeneratorPatchDraft,
            list[dict[str, Any]],
            dict[str, list[Any]],
        ] | None = None
        latest_errors: list[str] = []
        attempt_history: list[dict[str, Any]] = []

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
            for output_number in range(1, max_outputs + 1):
                response = self.client.invoke(
                    LLMRequest(
                        provider=self.model.provider,
                        model=self.model.model,
                        messages=fit_message_context(
                            messages,
                            model=self.model,
                        ).messages,
                        temperature=0,
                        max_tokens=self.model.max_tokens,
                        response_format="json",
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
                            span.set_output(
                                {
                                    "status": outcome.status,
                                    "outputs_used": outcome.outputs_used,
                                    "sample_count": len(samples),
                                    "samples": samples,
                                    "reference_pool_values": pool_values,
                                }
                            )
                            return outcome
                    else:
                        assert decision.patch is not None
                        # A new proposal invalidates prior sample acceptance even
                        # when this replacement cannot compile.
                        validated = None
                        try:
                            patch = _compile_patch(
                                decision.patch,
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
                            )
                            pool_values = _reference_pool_values(
                                reference_by_alias=prompt.reference_by_alias,
                                reference_values=reference_values,
                            )
                            validated = (patch, samples, pool_values)
                        except (KeyError, TypeError, ValueError) as exc:
                            errors = [str(exc)]
                        else:
                            messages.extend(
                                (
                                    LLMMessage(
                                        role="assistant",
                                        content=_response_json(response),
                                    ),
                                    LLMMessage(
                                        role="user",
                                        content=(
                                            "The complete Patch passed "
                                            "executable validation. Review these "
                                            f"exactly {case_count} generated "
                                            "parameter value groups against the "
                                            "root cause, desired behavior, and "
                                            "acceptance criteria:\n"
                                            + json.dumps(
                                                {
                                                    "parameter_value_groups": samples,
                                                    "reference_pool_values": pool_values,
                                                },
                                                ensure_ascii=False,
                                                separators=(",", ":"),
                                                default=str,
                                            )
                                            + "\nReturn action=accept, or action="
                                            "propose with one complete replacement."
                                        ),
                                    ),
                                )
                            )
                            continue

                latest_errors = errors or ["The Patch output could not be used."]
                messages.extend(
                    (
                        LLMMessage(
                            role="assistant",
                            content=_response_json(response),
                        ),
                        LLMMessage(
                            role="user",
                            content=(
                                "The previous output could not be used:\n"
                                + "\n".join(
                                    f"- {error}"
                                    for error in latest_errors[:_MAX_ERRORS]
                                )
                                + "\nReturn one complete corrected JSON object."
                            ),
                        ),
                    )
                )

            failure = ParameterPatchFailure(
                todo_id=task.todo_id,
                reason="output_budget_exhausted",
                outputs_used=max_outputs,
                errors=latest_errors[:_MAX_ERRORS],
                attempt_history=list(attempt_history),
            )
            span.set_output(failure.model_dump(mode="json"))
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


def _compile_constraint(
    expression: dict[str, Any],
    *,
    index: int,
    task: ParameterPatchTask,
    semantic: dict[str, str],
    config: OperationGeneratorConfig,
) -> CompiledConstraintPatch:
    """Compile one semantic Constraint and reject out-of-scope inputs."""
    allowed = set(task.affected_inputs)

    def convert(value: Any) -> Any:
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
    seed = int.from_bytes(
        hashlib.sha256(
            f"{candidate.operation_key}:{task.todo_id}".encode()
        ).digest()[:8],
        "big",
    )
    samples: list[dict[str, Any]] = []
    for case_index in range(case_count):
        generated = generate_test_case(
            candidate.snapshot,
            candidate,
            run_seed=seed,
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


def _response_json(response: LLMResponse) -> str:
    """Serialize the model's prior output for in-conversation correction."""
    value = (
        response.parsed_json
        if response.parsed_json is not None
        else response.content
        if response.content is not None
        else {}
    )
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    )
