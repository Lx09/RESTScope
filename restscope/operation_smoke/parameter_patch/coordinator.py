"""Coordinate proposal, deterministic validation, and independent review.

Failure Resolution decides why a batch failed and what behavior must change.
Parameter Patch converts that requirement into executable Generator and
Constraint objects. This deterministic Module uses the shared output guard and
owns feedback while the Patch Agent proposes and a fresh Review Agent judges
semantic alignment.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from restscope.tools import (
    OpenAPICapability,
    ResourceIdentifierCapability,
    ToolFailure,
)
from restscope.context import CompactTextWriter
from restscope.llm import LLMClient, LLMModelConfig
from restscope.observability import TracingRuntime
from restscope.operation_smoke.output_limit import ModelOutputLimit
from restscope.response_fields import ResponseFieldReference
from restscope.harness.testing import (
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
from restscope.harness.testing.generation import generate_test_case

from .agent import ParameterPatchAgent, ParameterPatchAttempt
from .prompts import build_parameter_patch_prompt
from .review import (
    ParameterPatchReviewAgent,
    ParameterPatchReviewCandidate,
)
from .schemas import (
    SelectedReferenceProvenance,
    CompiledConstraintPatch,
    GeneratorPatchDraft,
    ParameterPatchProposal,
    ParameterPatchTask,
    SemanticResponseValueGenerator,
    SemanticBooleanExpression,
    ValidatedParameterPatch,
)


_MAX_ERRORS = 20


class _CandidateReferenceValues:
    """Overlay unregistered response values on the normal generation provider."""

    def __init__(
        self,
        *,
        delegate: ReferenceValueProvider | None,
        response_values: Mapping[str, list[object]],
    ) -> None:
        """Keep candidate-only pools in memory for this coordination run."""
        self.delegate = delegate
        self.response_values = dict(response_values)

    def values_for(
        self,
        strategy: ResourceIdentifierGenerator | ResponseValueGenerator,
    ) -> list[object]:
        """Resolve candidate response pools first and delegate other strategies."""
        if isinstance(strategy, ResponseValueGenerator):
            values = self.response_values.get(strategy.value_name)
            if values is not None:
                return list(values)
        if self.delegate is None:
            return []
        return list(self.delegate.values_for(strategy))


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
        openapi_capability: OpenAPICapability | None = None,
        resource_capability: ResourceIdentifierCapability | None = None,
        tracing_runtime: TracingRuntime | None = None,
    ) -> None:
        """Store collaborators for one short-lived Patch coordination run."""
        self.client = client
        self.patch_model = patch_model
        self.review_model = review_model
        self.patch_system_prompt = patch_system_prompt
        self.review_system_prompt = review_system_prompt
        self.openapi_capability = openapi_capability
        self.resource_capability = resource_capability
        self.tracing_runtime = tracing_runtime or TracingRuntime.disabled()

    def run(
        self,
        *,
        task: ParameterPatchTask,
        config: OperationGeneratorConfig,
        active_constraints: list[CompiledConstraintPatch],
        case_count: int,
        reference_values: ReferenceValueProvider | None = None,
        random_seed: int = 0,
        output_limit: ModelOutputLimit,
    ) -> ValidatedParameterPatch:
        """Coordinate proposal, local validation, and fresh semantic review.

        Both Agents consume the Operation-wide hard limit. Compile failures and
        repeated calls remain in the same revision context until a reviewed
        candidate succeeds or the shared guard raises its terminal exception.
        """
        if not 1 <= case_count <= 20:
            raise ValueError("case_count must be between 1 and 20")
        if not self.patch_model.enabled or not self.review_model.enabled:
            raise RuntimeError("Both Parameter Patch models must be configured")

        prompt = build_parameter_patch_prompt(
            task=task,
            config=config,
            model=self.patch_model,
            active_constraints=active_constraints,
            system_prompt=self.patch_system_prompt,
        )
        patch_agent = ParameterPatchAgent(
            client=self.client,
            model=self.patch_model,
            prompt=prompt,
            openapi_capability=self.openapi_capability,
            resource_capability=self.resource_capability,
            output_limit=output_limit,
            tracing_runtime=self.tracing_runtime,
        )
        starting_outputs = output_limit.used
        latest_errors: list[str] = []
        attempt_history: list[dict[str, Any]] = []

        with self.tracing_runtime.span(
            "ParameterPatchCoordinator.run",
            kind="INTERNAL",
            input_value={
                "todo_id": task.todo_id,
                "input_count": len(task.affected_inputs),
                "case_count": case_count,
                "shared_outputs_used": starting_outputs,
            },
            attributes={
                "restscope.patch.todo_id": task.todo_id,
                "restscope.patch.input_count": len(task.affected_inputs),
                "restscope.patch.sample_count": case_count,
            },
        ) as span:
            while True:
                attempt = patch_agent.propose()
                attempt_history.append(_patch_attempt_record(attempt))
                errors = list(attempt.errors)

                # Lookup turns spend the shared model-output budget but do not
                # become candidate proposals. Tool execution remains read-only
                # and its complete provider call/result group stays in Context.
                if attempt.response.tool_calls and not errors:
                    patch_agent.execute_tools(attempt)
                    continue
                patch: GeneratorPatchDraft | None = None
                samples: list[dict[str, Any]] = []

                if attempt.submission is not None and not errors:
                    proposal = attempt.submission.patch
                    try:
                        patch, candidate_response_values = _compile_patch(
                            proposal,
                            task=task,
                            config=config,
                            queried_resources=patch_agent.queried_resources,
                            queried_response_fields=(
                                patch_agent.queried_response_fields
                            ),
                            reference_values=reference_values,
                            openapi_capability=self.openapi_capability,
                        )
                        samples = _sample_patch(
                            task=task,
                            config=config,
                            patch=patch,
                            active_constraints=active_constraints,
                            reference_values=_CandidateReferenceValues(
                                delegate=reference_values,
                                response_values=candidate_response_values,
                            ),
                            case_count=case_count,
                            random_seed=random_seed,
                        )
                    except (KeyError, TypeError, ValueError) as exc:
                        errors = [str(exc)]

                if patch is not None and not errors:
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
                        output_limit=output_limit,
                    )
                    attempt_history.extend(
                        {"agent": "parameter_patch_review_agent", **item}
                        for item in review.attempt_history
                    )
                    outputs_used = output_limit.used - starting_outputs
                    span.set_attribute(
                        "restscope.patch.shared_outputs_used", outputs_used
                    )
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
                                    patch.selected_reference_provenance
                                ),
                            }
                        )
                        return outcome
                    errors = list(review.issues)

                latest_errors = errors or ["The Patch proposal could not be used."]
                # Compiler errors and semantic Review issues both refer to the
                # exact proposal call. Returning them as its matching result
                # preserves the Patch Agent's one continuous revision context.
                patch_agent.append_feedback(
                    attempt,
                    _proposal_feedback(latest_errors),
                )


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
    writer.section(
        "REASONS THE PREVIOUS PATCH PROPOSAL WAS REJECTED",
        untrusted=True,
    )
    for index, error in enumerate(errors[:_MAX_ERRORS], start=1):
        writer.text(f"issue {index}", error)
    writer.section("REQUIRED REPLACEMENT PROPOSAL")
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
    # Keep field guidance next to the rejected output so a model using a
    # provider's JSON-only mode can repair a shape the provider did not enforce.
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
        "constraint fields",
        (
            "and, or, and cardinality use expressions, never conditions; "
            "not uses expression; implies uses condition and consequence."
        ),
    )
    return writer.render(max_chars=12_000).text


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
            "value_requirements": task.value_requirements,
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
                "value_count": option.value_count,
                "compatible_type": option.compatible_scalar_type,
                "producer_operations": option.producer_operation_keys,
                "status": option.producer_status_code,
                "media_type": option.producer_media_type,
                "field": option.source_field,
            }
            for option in patch.selected_reference_provenance
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
    output: dict[str, Any] = {}
    for handle in affected_inputs:
        item = by_node[semantic.node_by_handle[handle]]
        strategy = item.strategy.model_dump(mode="python")
        # Internal response pool names are deterministic storage identities.
        # The proposal and provenance show the producer field instead.
        if strategy.get("type") == "response_value":
            strategy = {"type": "response_value"}
        output[handle] = {
            "inclusion_probability": item.inclusion_probability,
            "strategy": strategy,
        }
    return output


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
    queried_resources: Mapping[str, tuple[frozenset[str], int]],
    queried_response_fields: set[tuple[str, str, str, str]],
    reference_values: ReferenceValueProvider | None,
    openapi_capability: OpenAPICapability | None,
) -> tuple[GeneratorPatchDraft, dict[str, list[object]]]:
    """Translate semantic output after revalidating any selected evidence."""
    semantic = build_semantic_input_map(config)
    allowed = set(task.affected_inputs)
    supplied = [change.input for change in proposal.changes]
    if len(supplied) != len(set(supplied)):
        raise ValueError("each semantic input may be changed at most once")
    updates: list[InputGeneratorPatch] = []
    selected_options: list[SelectedReferenceProvenance] = []
    candidate_response_values: dict[str, list[object]] = {}
    for change in proposal.changes:
        if change.input not in allowed:
            raise ValueError(
                f"{change.input} is outside the Resolution Patch requirement"
            )
        node_id = semantic.node_by_handle.get(change.input)
        if node_id is None:
            raise ValueError(f"Unknown semantic input: {change.input}")
        strategy = change.strategy
        if isinstance(strategy, ResourceIdentifierGenerator):
            if strategy.resource not in queried_resources:
                raise ValueError(
                    "Call resource.list_ids successfully before using "
                    f"canonical resource {strategy.resource!r}"
                )
            if reference_values is None:
                raise ValueError("Resource Identifier values are unavailable")
            current_values = reference_values.values_for(strategy)
            if not current_values:
                raise ValueError(
                    f"Resource Identifier pool is empty: {strategy.resource}"
                )
            expected_type = _reference_expected_type_for_input(
                config,
                node_id,
            )
            value_types = [_json_scalar_type(value) for value in current_values]
            if any(value_type is None for value_type in value_types) or not (
                _reference_types_compatible(expected_type, value_types)
            ):
                raise ValueError(
                    f"Resource Identifier values are incompatible with {change.input}"
                )
            selected_options.append(
                SelectedReferenceProvenance(
                    input_node_id=node_id,
                    kind="resource_identifier",
                    canonical_resource=strategy.resource,
                    compatible_scalar_type=(
                        expected_type
                        or "|".join(
                            sorted(
                                {
                                    item
                                    for item in value_types
                                    if item is not None
                                }
                            )
                        )
                    ),
                    value_count=len(current_values),
                )
            )
        if isinstance(strategy, SemanticResponseValueGenerator):
            source = strategy.source
            identity = (
                source.operation_key,
                source.matched_status_code,
                source.media_type,
                source.field,
            )
            if identity not in queried_response_fields:
                raise ValueError(
                    "The response_value source must be copied exactly from "
                    "openapi.find_observed_response_fields in this Patch session"
                )
            if not _response_source_is_current(openapi_capability, identity):
                raise ValueError(
                    "The selected response_value source is no longer available"
                )
            resolver = reference_values
            if resolver is None or not hasattr(resolver, "resolve_response_source"):
                raise ValueError("Response Value evidence is unavailable")
            option, values = resolver.resolve_response_source(
                config=config,
                input_node_id=node_id,
                operation_key=source.operation_key,
                matched_status_code=source.matched_status_code,
                media_type=source.media_type,
                field=source.field,
            )
            expected_selector = ResponseFieldReference.from_handle(
                source.field
            ).selector
            if (
                option.kind != "response_value"
                or option.producer_operation_keys != [source.operation_key]
                or option.producer_status_code != source.matched_status_code
                or option.producer_media_type != source.media_type
                or option.source_field != source.field
                or option.source_selector != expected_selector
            ):
                raise ValueError(
                    "Resolved response provenance does not match the selected source"
                )
            assert option.value_name is not None
            strategy = ResponseValueGenerator(
                type="response_value",
                value_name=option.value_name,
            )
            selected_options.append(option)
            candidate_response_values[option.value_name] = values
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
        selected_reference_provenance=selected_options,
    )
    preview_generator_patch(config, patch.updates)
    return patch, candidate_response_values


def _response_source_is_current(
    capability: OpenAPICapability | None,
    identity: tuple[str, str, str, str],
) -> bool:
    """Re-run current-IR observation lookup and require one exact source identity."""
    if capability is None:
        return False
    reference = ResponseFieldReference.from_handle(identity[3])
    query = ".".join(reference.property_names) or "body"
    offset = 0
    while True:
        try:
            result = capability.find_observed_response_fields(
                name=query,
                offset=offset,
                limit=200,
            )
        except ToolFailure:
            return False
        structured = result.get("structured")
        if not isinstance(structured, dict):
            return False
        for response in structured.get("responses", []):
            if not isinstance(response, dict):
                continue
            prefix = (
                response.get("operation_key"),
                response.get("matched_status_code"),
                response.get("media_type"),
            )
            for field in response.get("fields", []):
                if (
                    isinstance(field, dict)
                    and (*prefix, field.get("field")) == identity
                ):
                    return True
        next_offset = structured.get("next_offset")
        if not isinstance(next_offset, int) or next_offset <= offset:
            break
        offset = next_offset
    return False


def _reference_expected_type_for_input(
    config: OperationGeneratorConfig,
    input_node_id: str,
) -> str | None:
    """Return the exact body scalar type, or ``None`` for stringified parameters.

    OpenAPI path, query, header, and cookie parameters serialize scalar values
    as text. JSON request-body fields retain their declared scalar type, so the
    selected observed pool must match it exactly (integers may satisfy number).
    """
    parameter_ids = {
        item.input_node_id for item in config.snapshot.parameters
    }
    if input_node_id in parameter_ids:
        return None
    node = next(
        item
        for item in config.snapshot.input_nodes
        if item.input_node_id == input_node_id
    )
    schema = node.schema_contract
    declared = schema.type if schema is not None else None
    if isinstance(declared, list):
        concrete = [item for item in declared if item != "null"]
        declared = concrete[0] if len(concrete) == 1 else None
    if declared not in {"string", "integer", "number", "boolean"}:
        raise ValueError("Reference values can only target scalar inputs")
    return declared


def _json_scalar_type(value: object) -> str | None:
    """Classify one observed JSON scalar without treating Boolean as integer."""
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    return None


def _reference_types_compatible(
    expected_type: str | None,
    value_types: list[str | None],
) -> bool:
    """Require every value that the Generator may choose to fit the consumer."""
    if not value_types or any(item is None for item in value_types):
        return False
    if expected_type is None:
        return True
    return all(
        item == expected_type
        or (expected_type == "number" and item == "integer")
        for item in value_types
    )


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
    expression: SemanticBooleanExpression,
    *,
    task: ParameterPatchTask,
    semantic: Mapping[str, str],
    config: OperationGeneratorConfig,
) -> CompiledConstraintPatch:
    """Compile one model-facing Constraint into the Testing Module contract.

    The Patch model names inputs with readable semantic handles. This function
    converts those handles into the frozen input-node identities used during
    generation, rejects handles outside the approved Patch task, and then runs
    the existing executable Constraint validation and normalization.
    """
    allowed = set(task.affected_inputs)
    source = expression.model_dump(mode="python")

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
                    f"{handle} is outside the Resolution Patch requirement"
                )
            output.pop("input", None)
            output["input_node_id"] = semantic[handle]
        return output

    compiled = ConstraintSet.model_validate(
        {"constraints": [convert(source)]}
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
    return sample_compiled_patch(
        config=config,
        patch=patch,
        active_constraints=active_constraints,
        affected_parameters=task.affected_inputs,
        reference_values=reference_values,
        case_count=case_count,
        random_seed=random_seed,
    )


def sample_compiled_patch(
    *,
    config: OperationGeneratorConfig,
    patch: GeneratorPatchDraft,
    active_constraints: list[CompiledConstraintPatch],
    affected_parameters: list[str],
    reference_values: ReferenceValueProvider | None,
    case_count: int,
    random_seed: int,
) -> list[dict[str, Any]]:
    """Freshly sample a compiled Patch against the complete resulting state.

    Failure Resolution finalization calls this after combining selected
    non-overlapping candidates. The function performs the same deterministic
    generation used before independent Review and returns only run-local sample
    evidence; it does not mutate Generator state or persist the samples.
    """
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
        for handle in affected_parameters:
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
