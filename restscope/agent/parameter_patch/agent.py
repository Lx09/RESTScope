"""Bounded Generator/Constraint construction for one Patch Group."""

from __future__ import annotations

import hashlib
import json
from typing import Any

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
    GeneratorPatchAttribution,
    GeneratorPatchDraft,
    ParameterPatchDecision,
    ParameterPatchProposal,
    PatchGroupFailure,
    PatchGroupTask,
    ValidatedPatchGroup,
)


PATCH_SAMPLE_COUNT = 10
_MAX_ERRORS = 20


class ParameterPatchAgent:
    """Construct, validate, sample, and self-review one isolated Patch Group.

    The model proposes complete Generator/Constraint patches, but deterministic
    code owns every safety boundary: allowed inputs, schema parsing, reference
    aliases, constraint satisfiability, compatibility with prior groups, and
    generation of exactly ten samples.  The same model may accept only after it
    has seen those samples.  No sample is sent to the target API or catalog.
    """

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

    def run(
        self,
        *,
        task: PatchGroupTask,
        config: OperationGeneratorConfig,
        active_constraints: list[CompiledConstraintPatch],
        reference_values: ReferenceValueProvider | None = None,
        reference_options: list[AvailableReferenceOption] | None = None,
        max_attempts: int = 20,
    ) -> ValidatedPatchGroup | PatchGroupFailure:
        """Run the bounded propose → validate → sample → review conversation."""
        if not 2 <= max_attempts <= 20:
            raise ValueError("max_attempts must be between 2 and 20")
        if not self.model.enabled:
            raise RuntimeError("The Parameter Patch model is not configured")
        prompt = build_parameter_patch_prompt(
            task=task,
            config=config,
            reference_options=list(reference_options or []),
        )
        messages = [
            LLMMessage(role="system", content=prompt.system),
            LLMMessage(role="user", content=prompt.user),
        ]
        # ``validated`` is deliberately reset by every proposal.  Therefore an
        # accept decision can refer only to the most recently compiled and
        # sampled complete patch.
        validated: tuple[
            GeneratorPatchDraft,
            list[dict[str, Any]],
            dict[str, list[Any]],
        ] | None = None
        latest_errors: list[str] = []
        last_candidate_signature: str | None = None
        repeated_candidates = 0

        with self.tracing_runtime.span(
            "ParameterPatchAgent.run",
            kind="AGENT",
            input_value={
                "group_id": task.group_id,
                "item_count": len(task.item_ids),
                "input_count": len(task.inputs),
                "max_attempts": max_attempts,
            },
            attributes={
                "restscope.patch.group_id": task.group_id,
                "restscope.patch.item_count": len(task.item_ids),
                "restscope.patch.input_count": len(task.inputs),
            },
        ) as span:
            for attempt in range(1, max_attempts + 1):
                # Structured roles use temperature zero to reduce schema drift
                # and make repeated-candidate detection meaningful.
                response = self.client.invoke(
                    LLMRequest(
                        provider=self.model.provider,
                        model=self.model.model,
                        messages=messages,
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
                decision, errors = self._parse(response)
                if decision is None or decision.action != "propose":
                    last_candidate_signature = None
                    repeated_candidates = 0
                if decision is not None and not errors:
                    if decision.action == "accept":
                        if validated is None:
                            errors = [
                                "accept requires validated sample feedback"
                            ]
                        else:
                            patch, samples, reference_pool_values = validated
                            result = ValidatedPatchGroup(
                                group_id=task.group_id,
                                item_ids=task.item_ids,
                                root_failure_refs=task.root_failure_refs,
                                patch=patch,
                                samples=samples,
                                attempts=attempt,
                            )
                            span.set_output(
                                {
                                    "status": result.status,
                                    "attempts": result.attempts,
                                    "sample_count": len(result.samples),
                                    "samples": result.samples,
                                    "reference_pool_values": (
                                        reference_pool_values
                                    ),
                                }
                            )
                            span.set_attribute(
                                "restscope.patch.attempts",
                                result.attempts,
                            )
                            span.set_attribute(
                                "restscope.patch.validation_result",
                                "validated",
                            )
                            return result
                    else:
                        assert decision.patch is not None
                        # A complete revision replaces the prior candidate even
                        # when compilation or sampling later rejects it.
                        validated = None
                        try:
                            # Compilation translates model-facing semantic
                            # handles into internal node IDs.  Sampling then
                            # checks the patch together with constraints already
                            # accepted from earlier groups.
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
                            )
                            reference_pool_values = _reference_pool_values(
                                reference_by_alias=(
                                    prompt.reference_by_alias
                                ),
                                reference_values=reference_values,
                            )
                            validated = (
                                patch,
                                samples,
                                reference_pool_values,
                            )
                        except (KeyError, TypeError, ValueError) as exc:
                            errors = [str(exc)]
                        else:
                            signature = _candidate_signature(
                                decision.patch,
                                [],
                            )
                            if signature == last_candidate_signature:
                                repeated_candidates += 1
                            else:
                                last_candidate_signature = signature
                                repeated_candidates = 1
                            # A model that returns the same already-valid patch
                            # three times without accepting it is stalled; stop
                            # early instead of spending the full attempt budget.
                            if repeated_candidates >= 3:
                                failure = PatchGroupFailure(
                                    group_id=task.group_id,
                                    item_ids=task.item_ids,
                                    root_failure_refs=(
                                        task.root_failure_refs
                                    ),
                                    reason="stalled_candidate",
                                    attempts=attempt,
                                    errors=[],
                                )
                                span.set_output(
                                    {
                                        "status": failure.status,
                                        "reason": failure.reason,
                                        "attempts": failure.attempts,
                                    }
                                )
                                span.set_attribute(
                                    "restscope.patch.attempts",
                                    failure.attempts,
                                )
                                span.set_attribute(
                                    "restscope.patch.validation_result",
                                    failure.reason,
                                )
                                return failure
                            messages.extend(
                                (
                                    LLMMessage(
                                        role="assistant",
                                        content=_response_json(response),
                                    ),
                                    LLMMessage(
                                        role="user",
                                        content=(
                                            "The complete patch passed local "
                                            "validation. Review these exactly "
                                            "10 generated parameter value "
                                            "groups:\n"
                                            + json.dumps(
                                                {
                                                    "parameter_value_groups": (
                                                        samples
                                                    ),
                                                    "reference_pool_values": (
                                                        reference_pool_values
                                                    ),
                                                },
                                                ensure_ascii=False,
                                                separators=(",", ":"),
                                                default=str,
                                            )
                                            + "\nReturn action=accept if they "
                                            "satisfy every task requirement; "
                                            "otherwise return action=propose "
                                            "with one complete replacement patch."
                                        ),
                                    ),
                                )
                            )
                            continue
                # Invalid output is returned to the same isolated conversation
                # with concrete errors.  Nothing from this group is shared with
                # another Parameter Patch Agent.
                latest_errors = errors or ["The model output could not be used"]
                if (
                    decision is not None
                    and decision.action == "propose"
                    and decision.patch is not None
                ):
                    signature = _candidate_signature(
                        decision.patch,
                        latest_errors,
                    )
                    if signature == last_candidate_signature:
                        repeated_candidates += 1
                    else:
                        last_candidate_signature = signature
                        repeated_candidates = 1
                    if repeated_candidates >= 3:
                        failure = PatchGroupFailure(
                            group_id=task.group_id,
                            item_ids=task.item_ids,
                            root_failure_refs=task.root_failure_refs,
                            reason="stalled_candidate",
                            attempts=attempt,
                            errors=latest_errors[:_MAX_ERRORS],
                        )
                        span.set_output(
                            {
                                "status": failure.status,
                                "reason": failure.reason,
                                "attempts": failure.attempts,
                            }
                        )
                        span.set_attribute(
                            "restscope.patch.attempts",
                            failure.attempts,
                        )
                        span.set_attribute(
                            "restscope.patch.validation_result",
                            failure.reason,
                        )
                        return failure
                messages.extend(
                    (
                        LLMMessage(
                            role="assistant",
                            content=_response_json(response),
                        ),
                        LLMMessage(
                            role="user",
                            content=(
                                "Your previous output could not be used:\n"
                                + "\n".join(
                                    f"- {error}"
                                    for error in latest_errors[:_MAX_ERRORS]
                                )
                                + "\nReturn one complete corrected JSON object."
                            ),
                        ),
                    )
                )

            failure = PatchGroupFailure(
                group_id=task.group_id,
                item_ids=task.item_ids,
                root_failure_refs=task.root_failure_refs,
                reason="attempt_limit",
                attempts=max_attempts,
                errors=latest_errors[:_MAX_ERRORS],
            )
            span.set_output(
                {
                    "status": failure.status,
                    "reason": failure.reason,
                    "attempts": failure.attempts,
                }
            )
            span.set_attribute("restscope.patch.attempts", failure.attempts)
            span.set_attribute(
                "restscope.patch.validation_result",
                failure.reason,
            )
            return failure

    def _parse(
        self,
        response: LLMResponse,
    ) -> tuple[ParameterPatchDecision | None, list[str]]:
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


def _candidate_signature(
    proposal: ParameterPatchProposal,
    errors: list[str],
) -> str:
    return json.dumps(
        {
            "patch": proposal.model_dump(mode="json"),
            "errors": [" ".join(error.split()) for error in errors],
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _compile_patch(
    proposal: ParameterPatchProposal,
    *,
    task: PatchGroupTask,
    config: OperationGeneratorConfig,
    reference_by_alias: dict[str, AvailableReferenceOption],
) -> GeneratorPatchDraft:
    """Translate model-facing handles and aliases into validated runtime objects.

    This is the trust boundary between free-form model output and the testing
    engine.  It rejects edits outside the assigned group, duplicate edits,
    system-managed generators, invented observed-value sources, and malformed
    constraints before previewing the patch against the current configuration.
    """
    semantic = build_semantic_input_map(config)
    allowed = set(task.inputs)
    supplied = [change.input for change in proposal.changes]
    if len(set(supplied)) != len(supplied):
        raise ValueError("each semantic input may be changed at most once")
    updates: list[InputGeneratorPatch] = []
    attributions: list[GeneratorPatchAttribution] = []
    selected_reference_options: list[AvailableReferenceOption] = []
    for change in proposal.changes:
        if change.input not in allowed:
            raise ValueError(
                f"{change.input} is outside Patch Group {task.group_id}"
            )
        node_id = semantic.node_by_handle.get(change.input)
        if node_id is None:
            raise ValueError(f"Unknown semantic input: {change.input}")
        strategy = change.strategy
        if strategy is not None and strategy.type in {
            "object",
            "request_body",
        }:
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
            selected_reference_options.append(option)
        updates.append(
            InputGeneratorPatch(
                input_node_id=node_id,
                inclusion_probability=change.inclusion_probability,
                strategy=strategy,
            )
        )
        attributions.append(
            GeneratorPatchAttribution(
                input_node_id=node_id,
                group_ids=[task.group_id],
                item_ids=task.item_ids,
                root_failure_refs=task.root_failure_refs,
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
        attributions=attributions,
        constraints=constraints,
        selected_reference_options=selected_reference_options,
    )
    preview_generator_patch(config, patch.updates)
    return patch


def _compile_constraint(
    expression: dict[str, Any],
    *,
    index: int,
    task: PatchGroupTask,
    semantic,
    config: OperationGeneratorConfig,
) -> CompiledConstraintPatch:
    """
    Compile constraint for one isolated Generator and Constraint Patch Group.

    This private helper keeps one transformation or policy decision explicit so the
    surrounding orchestration remains readable.
    """
    referenced: list[str] = []

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
            if handle not in task.inputs:
                raise ValueError(
                    f"{handle} is outside Patch Group {task.group_id}"
                )
            referenced.append(handle)
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
        f"{task.group_id}:{index}:{encoded}".encode()
    ).hexdigest()[:16]
    return CompiledConstraintPatch(
        constraint_id=f"constraint_{identity}",
        group_ids=[task.group_id],
        item_ids=task.item_ids,
        root_failure_refs=task.root_failure_refs,
        kind=classify_constraint(normalized.constraints[0]),
        constraint=normalized,
    )


def _sample_patch(
    *,
    task: PatchGroupTask,
    config: OperationGeneratorConfig,
    patch: GeneratorPatchDraft,
    active_constraints: list[CompiledConstraintPatch],
    reference_values: ReferenceValueProvider | None,
) -> list[dict[str, Any]]:
    """
    Handle sample patch as part of one isolated Generator and Constraint Patch Group.

    This private helper keeps one transformation or policy decision explicit so the
    surrounding orchestration remains readable.
    """
    candidate = preview_generator_patch(config, patch.updates)
    all_constraints = [
        expression
        for item in [*active_constraints, *patch.constraints]
        for expression in item.constraint.constraints
    ]
    constraints = (
        ConstraintSet(constraints=all_constraints)
        if all_constraints
        else None
    )
    semantic = build_semantic_input_map(candidate)
    seed = int.from_bytes(
        hashlib.sha256(
            f"{candidate.operation_key}:{task.group_id}".encode()
        ).digest()[:8],
        "big",
    )
    samples: list[dict[str, Any]] = []
    for case_index in range(PATCH_SAMPLE_COUNT):
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
        sample_values: dict[str, Any] = {}
        sample_presence: dict[str, bool] = {}
        for handle in task.inputs:
            node_id = semantic.node_by_handle[handle]
            assignment = assignments[node_id]
            sample_presence[handle] = assignment.present
            if assignment.present:
                sample_values[handle] = project_generated_input_value(
                    candidate.snapshot,
                    generated,
                    input_node_id=node_id,
                )
            else:
                sample_values[handle] = None
        samples.append(
            {
                "values": sample_values,
                "present": sample_presence,
            }
        )
    explicitly_mandatory_handles = {
        semantic.handle_by_node[update.input_node_id]
        for update in patch.updates
        if update.inclusion_probability == 1
        and update.input_node_id in semantic.handle_by_node
    }
    missing_handles = sorted(
        handle
        for handle in explicitly_mandatory_handles
        if any(not sample["present"].get(handle, False) for sample in samples)
    )
    if missing_handles:
        raise ValueError(
            "Explicit inclusion_probability=1 inputs were absent from local "
            f"samples: {missing_handles}"
        )
    return samples


def _reference_pool_values(
    *,
    reference_by_alias: dict[str, AvailableReferenceOption],
    reference_values: ReferenceValueProvider | None,
) -> dict[str, list[Any]]:
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
    value = (
        response.parsed_json
        if response.parsed_json is not None
        else response.content
        if response.content is not None
        else {}
    )
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
