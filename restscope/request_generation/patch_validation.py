"""Compile, sample, validate, and atomically apply semantic Parameter Patches.

``ParameterPatchRuntime`` is the deep Module behind the two read-only Request
Generation Tools and the one mutating Patch Tool. It translates semantic input
handles to frozen OpenAPI nodes, validates Generator and recursive Constraint
semantics, resolves reference-backed values, creates deterministic witnesses,
and applies only an exact previously validated revision/digest pair.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
from typing import Any

from restscope.operation_references import ResponseFieldReference
from restscope.openapi_parser import OpenAPISpecIR

from .constraints import (
    ConstraintSet,
    OperationConstraintRecord,
    classify_constraint,
    normalize_constraint_set,
    referenced_input_node_ids,
    validate_constraint_set,
)
from .generation import generate_test_case
from .models import (
    InputGeneratorPatch,
    OperationGeneratorConfig,
    ResourceIdentifierGenerator,
    ResponseValueGenerator,
)
from .patch_models import (
    CompiledConstraintPatch,
    CompiledParameterPatch,
    SelectedReferenceProvenance,
    SemanticBooleanExpression,
    SemanticParameterPatch,
    SemanticResponseValueGenerator,
)
from .ports import (
    ObservedResponseFieldLookup,
    ReferenceValueProvider,
    ResourceIdentifierLookup,
)
from .semantics import build_semantic_input_map
from .store import (
    GeneratorConfigError,
    RequestGenerationConfigStore,
    RequestGenerationState,
    expand_generator_patch_presence,
    generation_state_digest,
    preview_generator_patch,
)
from .constraint_solver import assignments_from_generated_case
from .generation import project_generated_input_value


MAX_TOOL_OUTPUT_CHARACTERS = 24_000


class ParameterPatchValidationError(ValueError):
    """Describe a correctable semantic Patch problem with a stable code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class _CandidateReferenceValues:
    """Overlay unregistered response pools while deterministic samples run."""

    def __init__(
        self,
        *,
        delegate: ReferenceValueProvider | None,
        response_values: Mapping[str, list[object]],
    ) -> None:
        self.delegate = delegate
        self.response_values = dict(response_values)

    def values_for(
        self,
        strategy: ResourceIdentifierGenerator | ResponseValueGenerator,
    ) -> Sequence[object]:
        """Return candidate response values first, then current catalog values."""
        if isinstance(strategy, ResponseValueGenerator):
            values = self.response_values.get(strategy.value_name)
            if values is not None:
                return list(values)
        if self.delegate is None:
            return ()
        return tuple(self.delegate.values_for(strategy))


@dataclass(frozen=True, slots=True)
class ValidatedPatch:
    """Keep deterministic validation artifacts for Tool projection or Apply."""

    operation_key: str
    expected_revision: int
    state_digest: str
    affected_inputs: tuple[str, ...]
    semantic_patch: SemanticParameterPatch
    compiled_patch: CompiledParameterPatch
    final_config: OperationGeneratorConfig
    final_constraints: tuple[OperationConstraintRecord, ...]
    samples: tuple[dict[str, Any], ...]
    domain_analysis: tuple[dict[str, Any], ...]
    validation_digest: str
    seed: int
    sample_count: int


class ParameterPatchRuntime:
    """Provide one coherent validation and application Interface to Tools."""

    def __init__(
        self,
        *,
        store: RequestGenerationConfigStore,
        ir_provider: Callable[[], OpenAPISpecIR],
        reference_values: ReferenceValueProvider | None = None,
        openapi_backend: ObservedResponseFieldLookup | None = None,
        resource_backend: ResourceIdentifierLookup | None = None,
    ) -> None:
        self.store = store
        self.ir_provider = ir_provider
        self.reference_values = reference_values
        self.openapi_backend = openapi_backend
        self.resource_backend = resource_backend

    def validate(
        self,
        *,
        operation_key: str,
        expected_revision: int,
        affected_inputs: Sequence[str],
        patch: SemanticParameterPatch,
        seed: int = 0,
        sample_count: int = 5,
    ) -> ValidatedPatch:
        """Compile and sample one Patch against the exact current revision."""
        state = self.store.require_state(operation_key)
        if state.revision != expected_revision:
            raise ParameterPatchValidationError(
                "request_generation_state_conflict",
                "Generation state changed; read the current input state before validating again",
            )
        return self.validate_state(
            state=state,
            affected_inputs=affected_inputs,
            patch=patch,
            seed=seed,
            sample_count=sample_count,
        )

    def validate_state(
        self,
        *,
        state: RequestGenerationState,
        affected_inputs: Sequence[str],
        patch: SemanticParameterPatch,
        seed: int,
        sample_count: int,
    ) -> ValidatedPatch:
        """Validate against a caller-frozen state without reading Store again."""
        affected = _validate_affected_inputs(state.config, affected_inputs)
        if not 1 <= sample_count <= 20:
            raise ParameterPatchValidationError(
                "invalid_sample_count",
                "sample_count must be between 1 and 20",
            )
        compiled, candidate_values = self._compile(
            state=state,
            affected_inputs=affected,
            patch=patch,
        )
        final_config = preview_generator_patch(state.config, compiled.updates)
        final_constraints = _replace_constraint_scope(
            current=state.constraints,
            replacement=compiled.constraints,
            affected_node_ids={
                build_semantic_input_map(state.config).node_by_handle[item]
                for item in affected
            },
            operation_key=state.config.operation_key,
        )
        samples = _sample(
            config=final_config,
            constraints=final_constraints,
            affected_inputs=affected,
            reference_values=_CandidateReferenceValues(
                delegate=self.reference_values,
                response_values=candidate_values,
            ),
            seed=seed,
            sample_count=sample_count,
        )
        analysis = tuple(
            _domain_analysis(
                config=final_config,
                handle=handle,
                provenance=compiled.selected_reference_provenance,
            )
            for handle in affected
        )
        digest = _validation_digest(
            state=state,
            affected_inputs=affected,
            patch=patch,
            final_config=final_config,
            final_constraints=final_constraints,
            samples=samples,
            provenance=compiled.selected_reference_provenance,
            seed=seed,
            sample_count=sample_count,
        )
        return ValidatedPatch(
            operation_key=state.config.operation_key,
            expected_revision=state.revision,
            state_digest=state.state_digest,
            affected_inputs=affected,
            semantic_patch=patch,
            compiled_patch=compiled,
            final_config=final_config,
            final_constraints=final_constraints,
            samples=tuple(samples),
            domain_analysis=analysis,
            validation_digest=digest,
            seed=seed,
            sample_count=sample_count,
        )

    def apply(
        self,
        *,
        operation_key: str,
        expected_revision: int,
        validation_digest: str,
        affected_inputs: Sequence[str],
        patch: SemanticParameterPatch,
        seed: int = 0,
        sample_count: int = 5,
    ) -> tuple[RequestGenerationState, ValidatedPatch, list[dict[str, object]]]:
        """Revalidate and atomically apply the exact Patch named by a digest."""
        def prepare(
            state: RequestGenerationState,
        ) -> tuple[
            OperationGeneratorConfig,
            tuple[OperationConstraintRecord, ...],
            str,
            tuple[ValidatedPatch, list[dict[str, object]]],
        ]:
            """Repeat every validation and registration while writes are locked."""
            validated = self.validate_state(
                state=state,
                affected_inputs=affected_inputs,
                patch=patch,
                seed=seed,
                sample_count=sample_count,
            )
            if validated.validation_digest != validation_digest:
                raise ParameterPatchValidationError(
                    "parameter_patch_validation_digest_mismatch",
                    "The Patch inputs or current evidence differ from the validated content",
                )
            source_summaries: list[dict[str, object]] = []
            provider = self.reference_values
            if validated.compiled_patch.selected_reference_provenance:
                if provider is None or not hasattr(provider, "register_updates"):
                    raise ParameterPatchValidationError(
                        "reference_registration_unavailable",
                        "Reference-backed Patch application is unavailable",
                    )
                finalized, source_summaries = provider.register_updates(
                    ir=self.ir_provider(),
                    config=state.config,
                    updates=validated.compiled_patch.updates,
                    selected_reference_provenance=list(
                        validated.compiled_patch.selected_reference_provenance
                    ),
                )
                if finalized != validated.compiled_patch.updates:
                    raise ParameterPatchValidationError(
                        "reference_registration_drift",
                        "Registered response pool names differ from validated Generator state",
                    )
            return (
                validated.final_config,
                validated.final_constraints,
                validation_digest,
                (validated, source_summaries),
            )

        applied, prepared = self.store.apply_validated(
            operation_key=operation_key,
            expected_revision=expected_revision,
            prepare=prepare,
        )
        assert prepared is not None
        validated, source_summaries = prepared
        return applied, validated, source_summaries

    def _compile(
        self,
        *,
        state: RequestGenerationState,
        affected_inputs: tuple[str, ...],
        patch: SemanticParameterPatch,
    ) -> tuple[CompiledParameterPatch, dict[str, list[object]]]:
        """Compile semantic handles and revalidate all reference evidence."""
        config = state.config
        semantic = build_semantic_input_map(config)
        allowed = set(affected_inputs)
        supplied = [change.input for change in patch.changes]
        if len(supplied) != len(set(supplied)):
            raise ParameterPatchValidationError(
                "duplicate_patch_input",
                "Each semantic input may be changed at most once",
            )
        updates: list[InputGeneratorPatch] = []
        provenance: list[SelectedReferenceProvenance] = []
        candidate_values: dict[str, list[object]] = {}
        for change in patch.changes:
            if change.input not in allowed:
                raise ParameterPatchValidationError(
                    "patch_input_out_of_scope",
                    f"{change.input} is outside affected_inputs",
                )
            node_id = semantic.node_by_handle[change.input]
            strategy: Any = change.strategy
            if isinstance(strategy, ResourceIdentifierGenerator):
                option = self._validate_resource_reference(
                    config=config,
                    input_node_id=node_id,
                    handle=change.input,
                    strategy=strategy,
                )
                provenance.append(option)
            elif isinstance(strategy, SemanticResponseValueGenerator):
                option, values = self._validate_response_reference(
                    config=config,
                    input_node_id=node_id,
                    strategy=strategy,
                )
                assert option.value_name is not None
                strategy = ResponseValueGenerator(
                    type="response_value",
                    value_name=option.value_name,
                )
                provenance.append(option)
                candidate_values[option.value_name] = values
            updates.append(
                InputGeneratorPatch(
                    input_node_id=node_id,
                    inclusion_probability=change.inclusion_probability,
                    strategy=strategy,
                )
            )
        expanded = expand_generator_patch_presence(config, updates) if updates else []
        expanded_ids = {item.input_node_id for item in expanded}
        allowed_ids = {semantic.node_by_handle[item] for item in allowed}
        missing_scope = sorted(expanded_ids - allowed_ids)
        if missing_scope:
            missing_handles = [
                semantic.handle_by_node.get(item, item) for item in missing_scope
            ]
            raise ParameterPatchValidationError(
                "affected_input_scope_incomplete",
                "affected_inputs must include mandatory ancestors: "
                + ", ".join(missing_handles),
            )
        _validate_variant_branch_updates(
            config=config,
            updates=updates,
            handle_by_node=semantic.handle_by_node,
        )
        constraints = [
            _compile_constraint(
                item.expression,
                allowed_handles=allowed,
                semantic=semantic.node_by_handle,
                config=config,
            )
            for item in patch.constraints
        ]
        compiled = CompiledParameterPatch(
            updates=expanded,
            constraints=constraints,
            selected_reference_provenance=provenance,
        )
        preview_generator_patch(config, compiled.updates)
        return compiled, candidate_values

    def _validate_resource_reference(
        self,
        *,
        config: OperationGeneratorConfig,
        input_node_id: str,
        handle: str,
        strategy: ResourceIdentifierGenerator,
    ) -> SelectedReferenceProvenance:
        """Require one canonical, populated, type-compatible resource pool."""
        if self.resource_backend is None or self.reference_values is None:
            raise ParameterPatchValidationError(
                "resource_reference_unavailable",
                "Resource Identifier evidence is unavailable",
            )
        result = self.resource_backend.list_ids(resource=strategy.resource, limit=200)
        structured = result.get("structured", {})
        if (
            structured.get("status") != "found"
            or structured.get("canonical_resource") != strategy.resource
        ):
            raise ParameterPatchValidationError(
                "resource_not_canonical",
                f"{strategy.resource!r} is not a populated canonical resource",
            )
        values = list(self.reference_values.values_for(strategy))
        if not values:
            raise ParameterPatchValidationError(
                "resource_pool_empty",
                f"Resource Identifier pool is empty: {strategy.resource}",
            )
        expected = _reference_expected_type(config, input_node_id)
        types = [_json_scalar_type(item) for item in values]
        if not _reference_types_compatible(expected, types):
            raise ParameterPatchValidationError(
                "resource_type_mismatch",
                f"Resource Identifier values are incompatible with {handle}",
            )
        return SelectedReferenceProvenance(
            input_node_id=input_node_id,
            kind="resource_identifier",
            canonical_resource=strategy.resource,
            compatible_scalar_type=expected or "|".join(sorted(set(types))),
            value_count=len(values),
        )

    def _validate_response_reference(
        self,
        *,
        config: OperationGeneratorConfig,
        input_node_id: str,
        strategy: SemanticResponseValueGenerator,
    ) -> tuple[SelectedReferenceProvenance, list[object]]:
        """Require one current observed producer with compatible non-empty data."""
        source = strategy.source
        identity = (
            source.operation_key,
            source.matched_status_code,
            source.media_type,
            source.field,
        )
        if not _response_source_is_current(self.openapi_backend, identity):
            raise ParameterPatchValidationError(
                "response_source_unavailable",
                "The selected response-value source is not currently observable",
            )
        provider = self.reference_values
        if provider is None or not hasattr(provider, "resolve_response_source"):
            raise ParameterPatchValidationError(
                "response_value_evidence_unavailable",
                "Response Value evidence is unavailable",
            )
        option, values = provider.resolve_response_source(
            config=config,
            input_node_id=input_node_id,
            operation_key=source.operation_key,
            matched_status_code=source.matched_status_code,
            media_type=source.media_type,
            field=source.field,
            ir=self.ir_provider(),
        )
        expected_selector = ResponseFieldReference.from_handle(source.field).selector
        if (
            option.producer_operation_keys != [source.operation_key]
            or option.producer_status_code != source.matched_status_code
            or option.producer_media_type != source.media_type
            or option.source_field != source.field
            or option.source_selector != expected_selector
        ):
            raise ParameterPatchValidationError(
                "response_source_identity_mismatch",
                "Resolved response provenance differs from the selected source",
            )
        return option, values


def _validate_affected_inputs(
    config: OperationGeneratorConfig,
    affected_inputs: Sequence[str],
) -> tuple[str, ...]:
    """Require 1–20 unique semantic handles present in the operation."""
    values = tuple(affected_inputs)
    if not 1 <= len(values) <= 20 or len(values) != len(set(values)):
        raise ParameterPatchValidationError(
            "invalid_affected_inputs",
            "affected_inputs must contain 1 to 20 unique semantic handles",
        )
    semantic = build_semantic_input_map(config)
    unknown = sorted(set(values) - set(semantic.node_by_handle))
    if unknown:
        raise ParameterPatchValidationError(
            "unknown_affected_inputs",
            "Unknown semantic inputs: " + ", ".join(unknown),
        )
    return values


def constraint_closure(
    state: RequestGenerationState,
    input_handles: Sequence[str],
) -> tuple[tuple[OperationConstraintRecord, ...], tuple[str, ...]]:
    """Return active Constraints transitively connected to selected inputs."""
    selected = _validate_affected_inputs(state.config, input_handles)
    semantic = build_semantic_input_map(state.config)
    frontier = {semantic.node_by_handle[item] for item in selected}
    included: dict[str, OperationConstraintRecord] = {}
    changed = True
    while changed:
        changed = False
        for item in state.constraints:
            if item.id in included:
                continue
            owners = set(item.owner_input_node_ids)
            if owners & frontier:
                included[item.id] = item
                frontier.update(owners)
                changed = True
    extra = sorted(
        semantic.handle_by_node[node_id]
        for node_id in frontier
        if semantic.handle_by_node[node_id] not in selected
    )
    return tuple(included[key] for key in sorted(included)), tuple(extra)


def semantic_state_payload(
    state: RequestGenerationState,
    input_handles: Sequence[str],
) -> dict[str, Any]:
    """Project selected Generators and their complete Constraint closure."""
    selected = _validate_affected_inputs(state.config, input_handles)
    semantic = build_semantic_input_map(state.config)
    configs = {item.input_node_id: item for item in state.config.configs}
    closure, extra = constraint_closure(state, selected)
    handles = tuple(dict.fromkeys((*selected, *extra)))
    payload = {
        "operation_key": state.config.operation_key,
        "revision": state.revision,
        "state_digest": state.state_digest,
        "last_applied_validation_digest": state.last_applied_validation_digest,
        "inputs": [
            {
                "input": handle,
                "generator": {
                    "inclusion_probability": configs[
                        semantic.node_by_handle[handle]
                    ].inclusion_probability,
                    "strategy": configs[
                        semantic.node_by_handle[handle]
                    ].strategy.model_dump(mode="json"),
                },
            }
            for handle in handles
        ],
        "constraints": [
            _semantic_constraint_record(item, semantic.handle_by_node)
            for item in closure
        ],
        "additional_constraint_inputs": list(extra),
    }
    _require_bounded_payload(payload)
    return payload


def validation_payload(validated: ValidatedPatch) -> dict[str, Any]:
    """Project complete post-Patch state and bounded deterministic witnesses."""
    semantic = build_semantic_input_map(validated.final_config)
    configs = {item.input_node_id: item for item in validated.final_config.configs}
    final_handles = tuple(
        dict.fromkeys(
            (
                *validated.affected_inputs,
                *(
                    semantic.handle_by_node[item.input_node_id]
                    for item in validated.compiled_patch.updates
                ),
            )
        )
    )
    payload = {
        "operation_key": validated.operation_key,
        "revision": validated.expected_revision,
        "state_digest": validated.state_digest,
        "validation_digest": validated.validation_digest,
        "affected_inputs": list(validated.affected_inputs),
        "final_generators": [
            {
                "input": handle,
                "inclusion_probability": configs[
                    semantic.node_by_handle[handle]
                ].inclusion_probability,
                "strategy": configs[
                    semantic.node_by_handle[handle]
                ].strategy.model_dump(mode="json"),
            }
            for handle in final_handles
        ],
        "domain_analysis": list(validated.domain_analysis),
        "final_constraints": [
            _semantic_constraint_record(item, semantic.handle_by_node)
            for item in validated.final_constraints
        ],
        "constraint_participants": sorted(
            {
                semantic.handle_by_node[node_id]
                for item in validated.final_constraints
                for node_id in item.owner_input_node_ids
            }
        ),
        "samples": list(validated.samples),
    }
    _require_bounded_payload(payload)
    return payload


def _compile_constraint(
    expression: SemanticBooleanExpression,
    *,
    allowed_handles: set[str],
    semantic: Mapping[str, str],
    config: OperationGeneratorConfig,
) -> CompiledConstraintPatch:
    """Compile one recursive semantic expression into normalized node IDs."""
    source = expression.model_dump(mode="python")

    def convert(value: Any) -> Any:
        if isinstance(value, list):
            return [convert(item) for item in value]
        if not isinstance(value, dict):
            return value
        output = {key: convert(item) for key, item in value.items()}
        if value.get("type") in {"present", "input_value"}:
            handle = value.get("input")
            if not isinstance(handle, str) or handle not in semantic:
                raise ParameterPatchValidationError(
                    "unknown_constraint_input",
                    f"Unknown Constraint input: {handle}",
                )
            if handle not in allowed_handles:
                raise ParameterPatchValidationError(
                    "constraint_input_out_of_scope",
                    f"Constraint input is outside affected_inputs: {handle}",
                )
            output.pop("input", None)
            output["input_node_id"] = semantic[handle]
        return output

    compiled = ConstraintSet.model_validate({"constraints": [convert(source)]})
    validate_constraint_set(compiled, config.snapshot)
    normalized = normalize_constraint_set(compiled)
    encoded = json.dumps(
        normalized.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
    )
    identity = hashlib.sha256(
        f"{config.operation_key}:{encoded}".encode("utf-8")
    ).hexdigest()[:16]
    return CompiledConstraintPatch(
        constraint_id=f"constraint_{identity}",
        kind=classify_constraint(normalized.constraints[0]),
        constraint=normalized,
    )


def _replace_constraint_scope(
    *,
    current: Sequence[OperationConstraintRecord],
    replacement: Sequence[CompiledConstraintPatch],
    affected_node_ids: set[str],
    operation_key: str,
) -> tuple[OperationConstraintRecord, ...]:
    """Replace the full direct and transitive Constraint closure."""
    frontier = set(affected_node_ids)
    replaced: set[str] = set()
    changed = True
    while changed:
        changed = False
        for item in current:
            if item.id in replaced:
                continue
            owners = set(item.owner_input_node_ids)
            if owners & frontier:
                replaced.add(item.id)
                frontier.update(owners)
                changed = True
    retained = [item for item in current if item.id not in replaced]
    added = [
        OperationConstraintRecord(
            id=item.constraint_id,
            operation_key=operation_key,
            owner_input_node_ids=sorted(referenced_input_node_ids(item.constraint)),
            kind=item.kind,
            constraint=item.constraint,
        )
        for item in replacement
    ]
    by_id = {item.id: item for item in (*retained, *added)}
    return tuple(by_id[key] for key in sorted(by_id))


def _sample(
    *,
    config: OperationGeneratorConfig,
    constraints: Sequence[OperationConstraintRecord],
    affected_inputs: Sequence[str],
    reference_values: ReferenceValueProvider,
    seed: int,
    sample_count: int,
) -> list[dict[str, Any]]:
    """Generate bounded witnesses from the complete final state."""
    expressions = [
        expression
        for item in constraints
        for expression in item.constraint.constraints
    ]
    constraint_set = ConstraintSet(constraints=expressions) if expressions else None
    semantic = build_semantic_input_map(config)
    samples: list[dict[str, Any]] = []
    for case_index in range(sample_count):
        generated = generate_test_case(
            config.snapshot,
            config,
            run_seed=seed,
            case_index=case_index,
            reference_values=reference_values,
            constraints=constraint_set,
        )
        assignments = assignments_from_generated_case(config.snapshot, generated)
        values: dict[str, Any] = {}
        presence: dict[str, bool] = {}
        for handle in affected_inputs:
            node_id = semantic.node_by_handle[handle]
            assignment = assignments[node_id]
            presence[handle] = assignment.present
            values[handle] = (
                project_generated_input_value(
                    config.snapshot,
                    generated,
                    input_node_id=node_id,
                )
                if assignment.present
                else None
            )
        samples.append(
            {"case_index": case_index, "presence": presence, "values": values}
        )
    return samples


def _domain_analysis(
    *,
    config: OperationGeneratorConfig,
    handle: str,
    provenance: Sequence[SelectedReferenceProvenance],
) -> dict[str, Any]:
    """Describe the entire possible domain of one final Generator."""
    semantic = build_semantic_input_map(config)
    node_id = semantic.node_by_handle[handle]
    item = next(value for value in config.configs if value.input_node_id == node_id)
    strategy = item.strategy.model_dump(mode="json")
    analysis: dict[str, Any] = {
        "input": handle,
        "inclusion_probability": item.inclusion_probability,
        "strategy_type": strategy["type"],
        "domain": strategy,
    }
    selected = next(
        (value for value in provenance if value.input_node_id == node_id),
        None,
    )
    if selected is not None:
        analysis["reference"] = {
            key: value
            for key, value in selected.model_dump(mode="json").items()
            if key != "input_node_id" and value not in (None, [], {})
        }
    return analysis


def _validation_digest(
    *,
    state: RequestGenerationState,
    affected_inputs: Sequence[str],
    patch: SemanticParameterPatch,
    final_config: OperationGeneratorConfig,
    final_constraints: Sequence[OperationConstraintRecord],
    samples: Sequence[dict[str, Any]],
    provenance: Sequence[SelectedReferenceProvenance],
    seed: int,
    sample_count: int,
) -> str:
    """Bind the exact revision, semantic input, sources, and witnesses."""
    payload = {
        "operation_key": state.config.operation_key,
        "revision": state.revision,
        "state_digest": state.state_digest,
        "affected_inputs": list(affected_inputs),
        "patch": patch.model_dump(mode="json"),
        "final_state_digest": generation_state_digest(final_config, final_constraints),
        "provenance": [item.model_dump(mode="json") for item in provenance],
        "seed": seed,
        "sample_count": sample_count,
        "samples": list(samples),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _response_source_is_current(
    backend: ObservedResponseFieldLookup | None,
    identity: tuple[str, str, str, str],
) -> bool:
    """Re-run observed-field lookup and require one exact identity."""
    if backend is None:
        return False
    reference = ResponseFieldReference.from_handle(identity[3])
    query = ".".join(reference.property_names) or "body"
    offset = 0
    while True:
        try:
            result = backend.find_observed_response_fields(
                name=query,
                offset=offset,
                limit=200,
            )
        except Exception:
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
                if isinstance(field, dict) and (*prefix, field.get("field")) == identity:
                    return True
        next_offset = structured.get("next_offset")
        if not isinstance(next_offset, int) or next_offset <= offset:
            return False
        offset = next_offset


def _reference_expected_type(
    config: OperationGeneratorConfig,
    input_node_id: str,
) -> str | None:
    """Return a body scalar type or ``None`` for stringified parameters."""
    if input_node_id in {item.input_node_id for item in config.snapshot.parameters}:
        return None
    node = next(
        item for item in config.snapshot.input_nodes if item.input_node_id == input_node_id
    )
    declared = node.schema_contract.type if node.schema_contract is not None else None
    if isinstance(declared, list):
        concrete = [item for item in declared if item != "null"]
        declared = concrete[0] if len(concrete) == 1 else None
    if declared not in {"string", "integer", "number", "boolean"}:
        raise ParameterPatchValidationError(
            "reference_input_not_scalar",
            "Reference values can only target scalar inputs",
        )
    return declared


def _json_scalar_type(value: object) -> str:
    """Classify one JSON scalar without treating Boolean as integer."""
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    return "non_scalar"


def _reference_types_compatible(expected: str | None, values: Sequence[str]) -> bool:
    """Require every possible referenced value to fit the consumer input."""
    if not values or "non_scalar" in values:
        return False
    if expected is None:
        return True
    return all(
        value == expected or (expected == "number" and value == "integer")
        for value in values
    )


def _validate_variant_branch_updates(
    *,
    config: OperationGeneratorConfig,
    updates: Sequence[InputGeneratorPatch],
    handle_by_node: Mapping[str, str],
) -> None:
    """Require exclusive parent Variant selection for every changed branch."""
    nodes = {item.input_node_id: item for item in config.snapshot.input_nodes}
    current_configs = {item.input_node_id: item for item in config.configs}
    updates_by_node = {item.input_node_id: item for item in updates}
    for changed_node_id in updates_by_node:
        branch_node_id = changed_node_id
        current = nodes[changed_node_id]
        while current.parent_node_id is not None:
            parent = nodes[current.parent_node_id]
            parent_config = current_configs[parent.input_node_id]
            if parent_config.strategy.type == "variant":
                parent_update = updates_by_node.get(parent.input_node_id)
                parent_strategy = parent_update.strategy if parent_update else None
                parent_handle = handle_by_node.get(
                    parent.input_node_id,
                    parent.canonical_path,
                )
                if parent_strategy is None or parent_strategy.type != "variant":
                    raise ParameterPatchValidationError(
                        "variant_selection_missing",
                        f"{parent_handle} must select the changed variant branch",
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
                    raise ParameterPatchValidationError(
                        "variant_selection_ambiguous",
                        f"{parent_handle} must exclusively select the changed branch",
                    )
            branch_node_id = parent.input_node_id
            current = parent


def _variant_branch_index(node: Any) -> int:
    """Return one branch's numeric OpenAPI position for stable ordering."""
    try:
        return int(node.canonical_path.rstrip("/").rsplit("/", 1)[-1])
    except ValueError:
        return 0


def _semantic_constraint_record(
    item: OperationConstraintRecord,
    handle_by_node: Mapping[str, str],
) -> dict[str, Any]:
    """Replace internal node IDs with semantic handles in one Tool result."""
    def convert(value: Any) -> Any:
        if isinstance(value, list):
            return [convert(child) for child in value]
        if not isinstance(value, dict):
            return value
        output = {key: convert(child) for key, child in value.items()}
        node_id = output.pop("input_node_id", None)
        if node_id is not None:
            output["input"] = handle_by_node[node_id]
        return output

    return {
        "id": item.id,
        "kind": item.kind,
        "expression": convert(item.constraint.model_dump(mode="json"))["constraints"],
    }


def _require_bounded_payload(payload: dict[str, Any]) -> None:
    """Fail closed rather than truncate semantic state or Constraint closure."""
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    if len(encoded) > MAX_TOOL_OUTPUT_CHARACTERS:
        raise ParameterPatchValidationError(
            "request_generation_output_too_large",
            "Complete Generator and Constraint state exceeds 24000 characters",
        )
