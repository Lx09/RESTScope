"""Compile, sample, validate, and atomically apply semantic Parameter Patches.

``RequestGenerationPatchRuntime`` is the deep Module behind the two read-only Request
Generation Tools and the one mutating Patch Tool. It translates semantic input
handles to frozen OpenAPI nodes, validates Generator and recursive Constraint
semantics, resolves reference-backed values, creates deterministic witnesses,
and applies only an exact previously validated revision/digest pair.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from contextlib import nullcontext
from dataclasses import dataclass
import hashlib
import json

from restscope.operation_references import ResponseFieldReference
from restscope.openapi_parser import OpenAPISpecIR

from ..constraints import (
    ConstraintSet,
    OperationConstraintRecord,
    classify_constraint,
    normalize_constraint_set,
    referenced_input_node_ids,
    validate_constraint_set,
)
from ..generation import generate_test_case
from ..models import (
    InputGeneratorPatch,
    OperationGeneratorConfig,
    ResourceIdentifierGenerator,
    ResponseValueGenerator,
)
from .models import (
    CompiledConstraintPatch,
    CompiledParameterPatch,
    SelectedReferenceProvenance,
    SemanticBooleanExpression,
    SemanticParameterPatch,
    SemanticResponseValueGenerator,
)
from .errors import ParameterPatchValidationError
from .compiler import (
    compile_constraint,
    json_scalar_type,
    reference_expected_type,
    reference_types_compatible,
    replace_constraint_scope,
    validate_affected_inputs,
    validate_variant_branch_updates,
)
from .projection import semantic_state_payload
from ..ports import (
    ReferenceValueProvider,
    ResourceIdentifierLookup,
)
from ..semantics import build_semantic_input_map
from ..store import (
    GeneratorConfigError,
    RequestGenerationConfigStore,
    RequestGenerationState,
    ReferenceValueBinding,
    expand_generator_patch_presence,
    generation_state_digest,
    preview_generator_patch,
)
from ..constraint_solver import assignments_from_generated_case
from ..generation import project_generated_input_value


class _CandidateReferenceValues:
    """Overlay unregistered response pools while deterministic samples run."""

    def __init__(
        self,
        *,
        delegate: ReferenceValueProvider | None,
        selected_values: Mapping[str, list[object]],
    ) -> None:
        self.delegate = delegate
        self.selected_values = dict(selected_values)
        self._captured: dict[str, tuple[object, ...]] = {}

    def values_for(
        self,
        strategy: ResourceIdentifierGenerator | ResponseValueGenerator,
    ) -> Sequence[object]:
        """Return candidate response values first, then current catalog values."""
        key = _strategy_cache_key(strategy)
        values = self.selected_values.get(key)
        if values is not None:
            return tuple(values)
        if self.delegate is None:
            return ()
        if key not in self._captured:
            self._captured[key] = tuple(self.delegate.values_for(strategy))
        return self._captured[key]

    def identifier_records(
        self,
        *,
        resource: str,
        identifier: str,
    ) -> Sequence[Mapping[str, object]]:
        """Freeze complete Identifier Records for correlated component generation."""
        if self.delegate is None:
            return ()
        return tuple(
            dict(item)
            for item in self.delegate.identifier_records(
                resource=resource,
                identifier=identifier,
            )
        )


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
    final_reference_bindings: tuple[ReferenceValueBinding, ...]
    samples: tuple[dict[str, object], ...]
    domain_analysis: tuple[dict[str, object], ...]
    validation_digest: str
    seed: int
    sample_count: int


@dataclass(frozen=True, slots=True)
class AppliedReferenceBinding:
    """Expose one final binding using a semantic input handle, never a node ID."""

    input: str
    kind: str
    canonical_resource: str | None = None
    identifier: str | None = None
    component: str | None = None
    producer_operation_key: str | None = None
    producer_status_code: str | None = None
    producer_media_type: str | None = None
    source_field: str | None = None


@dataclass(frozen=True, slots=True)
class AppliedParameterPatch:
    """Return complete stable facts from one successful atomic application."""

    state: RequestGenerationState
    validated: ValidatedPatch
    previous_revision: int
    generator_change_count: int
    constraint_change_count: int
    final_reference_bindings: tuple[AppliedReferenceBinding, ...]
    removed_response_value_inputs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _UnchangedReferenceState:
    """Provide the staged-update shape when a Patch has no reference writes."""

    updates: tuple[InputGeneratorPatch, ...]
    bindings: tuple[ReferenceValueBinding, ...]
    removed_response_value_inputs: tuple[str, ...] = ()


class RequestGenerationPatchRuntime:
    """Provide one coherent validation and application Interface to Tools."""

    def __init__(
        self,
        *,
        store: RequestGenerationConfigStore,
        ir_provider: Callable[[], OpenAPISpecIR],
        reference_values: ReferenceValueProvider | None = None,
        resource_backend: ResourceIdentifierLookup | None = None,
    ) -> None:
        self._store = store
        self._ir_provider = ir_provider
        self._reference_values = reference_values
        self._resource_backend = resource_backend

    def read_state(
        self,
        *,
        operation_key: str,
        input_handles: Sequence[str],
    ) -> dict[str, object]:
        """Return one bounded semantic state projection without exposing Store."""
        return semantic_state_payload(
            self._store.require_state(operation_key),
            input_handles,
        )

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
        state = self._store.require_state(operation_key)
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
        affected = validate_affected_inputs(state.config, affected_inputs)
        if not 1 <= sample_count <= 20:
            raise ParameterPatchValidationError(
                "invalid_sample_count",
                "sample_count must be between 1 and 20",
            )
        compiled, selected_values = self._compile(
            state=state,
            affected_inputs=affected,
            patch=patch,
        )
        final_config = preview_generator_patch(state.config, compiled.updates)
        final_constraints = replace_constraint_scope(
            current=state.constraints,
            replacement=compiled.constraints,
            affected_node_ids={
                build_semantic_input_map(state.config).node_by_handle[item]
                for item in affected
            },
            operation_key=state.config.operation_key,
        )
        final_bindings = _final_reference_bindings(
            state=state,
            updates=compiled.updates,
            provenance=compiled.selected_reference_provenance,
        )
        samples = _sample(
            config=final_config,
            constraints=final_constraints,
            affected_inputs=affected,
            reference_values=_CandidateReferenceValues(
                delegate=self._reference_values,
                selected_values=selected_values,
            ),
            seed=seed,
            sample_count=sample_count,
        )
        analysis = tuple(
            _domain_analysis(
                config=final_config,
                handle=handle,
                provenance=compiled.selected_reference_provenance,
                bindings=final_bindings,
            )
            for handle in affected
        )
        digest = _validation_digest(
            state=state,
            affected_inputs=affected,
            patch=patch,
            final_config=final_config,
            final_constraints=final_constraints,
            final_reference_bindings=final_bindings,
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
            final_reference_bindings=final_bindings,
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
    ) -> AppliedParameterPatch:
        """Revalidate, stage durable pools, publish, and commit as one operation."""
        with self._store._replacement_transaction(
            operation_key=operation_key,
            expected_revision=expected_revision,
        ) as replacement:
            state = replacement.before
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
            if generation_state_digest(
                validated.final_config,
                validated.final_constraints,
                validated.final_reference_bindings,
            ) == state.state_digest:
                raise GeneratorConfigError(
                    "generator_patch_no_change",
                    "The validated Patch does not change Generator, Constraint, or reference state",
                )
            provider = self._reference_values
            needs_reference_transaction = bool(
                validated.compiled_patch.selected_reference_provenance
                or any(
                    item.input_node_id
                    in {
                        update.input_node_id
                        for update in validated.compiled_patch.updates
                    }
                    for item in state.reference_bindings
                    if item.kind == "response_value"
                )
            )
            if needs_reference_transaction:
                if provider is None or not hasattr(provider, "stage_updates"):
                    raise ParameterPatchValidationError(
                        "reference_registration_unavailable",
                        "Reference-backed Patch application is unavailable",
                    )
                staged = provider.stage_updates(
                    config=state.config,
                    updates=validated.compiled_patch.updates,
                    current_bindings=state.reference_bindings,
                    selected_reference_provenance=list(
                        validated.compiled_patch.selected_reference_provenance
                    ),
                )
            else:
                staged = nullcontext(
                    _UnchangedReferenceState(
                        updates=tuple(validated.compiled_patch.updates),
                        bindings=validated.final_reference_bindings,
                    )
                )
            with staged as reference_update:
                if tuple(reference_update.updates) != tuple(
                    validated.compiled_patch.updates
                ):
                    raise ParameterPatchValidationError(
                        "reference_registration_drift",
                        "Registered response pool names differ from validated Generator state",
                    )
                if tuple(reference_update.bindings) != (
                    validated.final_reference_bindings
                ):
                    raise ParameterPatchValidationError(
                        "reference_registration_drift",
                        "Staged reference bindings differ from validated state",
                    )
                applied = replacement.publish(
                    config=validated.final_config,
                    constraints=validated.final_constraints,
                    reference_bindings=reference_update.bindings,
                    validation_digest=validation_digest,
                )
            return AppliedParameterPatch(
                state=applied,
                validated=validated,
                previous_revision=state.revision,
                generator_change_count=_generator_change_count(
                    state.config,
                    applied.config,
                ),
                constraint_change_count=_constraint_change_count(
                    state.constraints,
                    applied.constraints,
                ),
                final_reference_bindings=_applied_reference_bindings(applied),
                removed_response_value_inputs=tuple(
                    build_semantic_input_map(applied.config).handle_by_node[item]
                    for item in reference_update.removed_response_value_inputs
                ),
            )

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
            strategy: object = change.strategy
            if isinstance(strategy, ResourceIdentifierGenerator):
                option, values = self._validate_resource_reference(
                    config=config,
                    input_node_id=node_id,
                    handle=change.input,
                    strategy=strategy,
                )
                provenance.append(option)
                candidate_values[_strategy_cache_key(strategy)] = values
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
                candidate_values[_strategy_cache_key(strategy)] = values
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
        validate_variant_branch_updates(
            config=config,
            updates=updates,
            handle_by_node=semantic.handle_by_node,
        )
        constraints = [
            compile_constraint(
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
        self._validate_complete_resource_identifier_bindings(
            config=config,
            updates=updates,
        )
        return compiled, candidate_values

    def _validate_resource_reference(
        self,
        *,
        config: OperationGeneratorConfig,
        input_node_id: str,
        handle: str,
        strategy: ResourceIdentifierGenerator,
    ) -> tuple[SelectedReferenceProvenance, list[object]]:
        """Require one canonical, populated, type-compatible resource pool."""
        if self._resource_backend is None or self._reference_values is None:
            raise ParameterPatchValidationError(
                "resource_reference_unavailable",
                "Resource Identifier evidence is unavailable",
            )
        result = self._resource_backend.list_ids(resource=strategy.resource, limit=200)
        structured = result.get("structured", {})
        if (
            structured.get("status") != "found"
            or structured.get("canonical_resource") != strategy.resource
        ):
            raise ParameterPatchValidationError(
                "resource_not_canonical",
                f"{strategy.resource!r} is not a populated canonical resource",
            )
        records = [
            item
            for item in structured.get("ids", [])
            if isinstance(item, dict) and item.get("identifier") == strategy.identifier
        ]
        component_names = {
            component.get("name")
            for record in records
            for component in record.get("components", [])
            if isinstance(component, dict)
        }
        if strategy.component not in component_names:
            raise ParameterPatchValidationError(
                "resource_component_unknown",
                f"Identifier component is unavailable: {strategy.identifier}/{strategy.component}",
            )
        values = list(self._reference_values.values_for(strategy))
        if not values:
            raise ParameterPatchValidationError(
                "resource_pool_empty",
                f"Resource Identifier pool is empty: {strategy.resource}",
            )
        expected = reference_expected_type(config, input_node_id)
        types = [json_scalar_type(item) for item in values]
        if not reference_types_compatible(expected, types):
            raise ParameterPatchValidationError(
                "resource_type_mismatch",
                f"Resource Identifier values are incompatible with {handle}",
            )
        return (
            SelectedReferenceProvenance(
                input_node_id=input_node_id,
                kind="resource_identifier",
                canonical_resource=strategy.resource,
                identifier=strategy.identifier,
                component=strategy.component,
                compatible_scalar_type=expected or "|".join(sorted(set(types))),
                value_count=len(values),
            ),
            values,
        )

    def _validate_complete_resource_identifier_bindings(
        self,
        *,
        config: OperationGeneratorConfig,
        updates: list[InputGeneratorPatch],
    ) -> None:
        """Require each composite Definition to bind every path component once."""
        groups: dict[tuple[str, str], list[tuple[str, ResourceIdentifierGenerator]]] = {}
        for update in updates:
            if isinstance(update.strategy, ResourceIdentifierGenerator):
                groups.setdefault(
                    (update.strategy.resource, update.strategy.identifier), []
                ).append((update.input_node_id, update.strategy))
        if not groups:
            return
        if self._resource_backend is None:
            raise ParameterPatchValidationError(
                "resource_reference_unavailable",
                "Resource Identifier evidence is unavailable",
            )
        parameters = {item.input_node_id: item for item in config.snapshot.parameters}
        for (resource, identifier), bindings in groups.items():
            result = self._resource_backend.list_ids(resource=resource, limit=200)
            structured = result.get("structured", {})
            records = [
                item for item in structured.get("ids", [])
                if isinstance(item, dict) and item.get("identifier") == identifier
            ]
            expected = {
                component.get("name")
                for record in records
                for component in record.get("components", [])
                if isinstance(component, dict)
            }
            # A one-component Definition behaves like the original scalar
            # reference and may satisfy any schema-compatible input. Only a
            # composite Definition has the atomic path-binding requirement.
            if len(expected) <= 1:
                continue
            if any(
                parameters.get(node_id) is None
                or parameters[node_id].location != "path"
                for node_id, _strategy in bindings
            ):
                raise ParameterPatchValidationError(
                    "resource_composite_requires_path",
                    "Composite Resource Identifier components may bind only path parameters",
                )
            supplied = [strategy.component for _node_id, strategy in bindings]
            if not expected or len(supplied) != len(set(supplied)) or set(supplied) != expected:
                raise ParameterPatchValidationError(
                    "resource_identifier_binding_incomplete",
                    f"Patch must bind every component of {identifier} exactly once",
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
        provider = self._reference_values
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
            ir=self._ir_provider(),
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


def _final_reference_bindings(
    *,
    state: RequestGenerationState,
    updates: Sequence[InputGeneratorPatch],
    provenance: Sequence[SelectedReferenceProvenance],
) -> tuple[ReferenceValueBinding, ...]:
    """Derive the exact final binding set before any persistent write occurs."""
    changed = {item.input_node_id for item in updates}
    output = [
        item for item in state.reference_bindings if item.input_node_id not in changed
    ]
    for selected in provenance:
        if selected.kind == "resource_identifier":
            assert selected.canonical_resource is not None
            output.append(
                ReferenceValueBinding(
                    input_node_id=selected.input_node_id,
                    kind="resource_identifier",
                    value_name=selected.canonical_resource,
                    identifier=selected.identifier,
                    component=selected.component,
                )
            )
            continue
        assert selected.value_name is not None
        output.append(
            ReferenceValueBinding(
                input_node_id=selected.input_node_id,
                kind="response_value",
                value_name=selected.value_name,
                producer_operation_key=selected.producer_operation_keys[0],
                producer_status_code=selected.producer_status_code,
                producer_media_type=selected.producer_media_type,
                source_field=selected.source_field,
                source_selector=selected.source_selector,
            )
        )
    return tuple(sorted(output, key=lambda item: item.input_node_id))


def _strategy_cache_key(
    strategy: ResourceIdentifierGenerator | ResponseValueGenerator,
) -> str:
    """Identify one reference strategy for a validation-local immutable pool."""
    return json.dumps(
        strategy.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
    )


def _applied_reference_bindings(
    state: RequestGenerationState,
) -> tuple[AppliedReferenceBinding, ...]:
    """Translate final internal binding identities to stable semantic handles."""
    handles = build_semantic_input_map(state.config).handle_by_node
    return tuple(
        AppliedReferenceBinding(
            input=handles[item.input_node_id],
            kind=item.kind,
            canonical_resource=(
                item.value_name if item.kind == "resource_identifier" else None
            ),
            identifier=item.identifier,
            component=item.component,
            producer_operation_key=item.producer_operation_key,
            producer_status_code=item.producer_status_code,
            producer_media_type=item.producer_media_type,
            source_field=item.source_field,
        )
        for item in state.reference_bindings
    )


def _generator_change_count(
    previous: OperationGeneratorConfig,
    current: OperationGeneratorConfig,
) -> int:
    """Count complete per-input Generator changes for the Apply result."""
    old = {item.input_node_id: item for item in previous.configs}
    return sum(old.get(item.input_node_id) != item for item in current.configs)


def _constraint_change_count(
    previous: Sequence[OperationConstraintRecord],
    current: Sequence[OperationConstraintRecord],
) -> int:
    """Count added, removed, or replaced active Constraint records."""
    old = {item.id: item for item in previous}
    new = {item.id: item for item in current}
    return len(
        {
            key
            for key in old.keys() | new.keys()
            if old.get(key) != new.get(key)
        }
    )


def _sample(
    *,
    config: OperationGeneratorConfig,
    constraints: Sequence[OperationConstraintRecord],
    affected_inputs: Sequence[str],
    reference_values: ReferenceValueProvider,
    seed: int,
    sample_count: int,
) -> list[dict[str, object]]:
    """Generate bounded witnesses from the complete final state."""
    expressions = [
        expression
        for item in constraints
        for expression in item.constraint.constraints
    ]
    constraint_set = ConstraintSet(constraints=expressions) if expressions else None
    semantic = build_semantic_input_map(config)
    samples: list[dict[str, object]] = []
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
        values: dict[str, object] = {}
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
    bindings: Sequence[ReferenceValueBinding],
) -> dict[str, object]:
    """Describe the entire possible domain of one final Generator."""
    semantic = build_semantic_input_map(config)
    node_id = semantic.node_by_handle[handle]
    item = next(value for value in config.configs if value.input_node_id == node_id)
    strategy = item.strategy.model_dump(mode="json")
    binding = next(
        (value for value in bindings if value.input_node_id == node_id),
        None,
    )
    if binding is not None and binding.kind == "response_value":
        strategy = {
            "type": "response_value",
            "source": {
                "operation_key": binding.producer_operation_key,
                "matched_status_code": binding.producer_status_code,
                "media_type": binding.producer_media_type,
                "field": binding.source_field,
            },
        }
    analysis: dict[str, object] = {
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
    final_reference_bindings: Sequence[ReferenceValueBinding],
    samples: Sequence[dict[str, object]],
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
        "final_state_digest": generation_state_digest(
            final_config,
            final_constraints,
            final_reference_bindings,
        ),
        "provenance": [item.model_dump(mode="json") for item in provenance],
        "seed": seed,
        "sample_count": sample_count,
        "samples": list(samples),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
