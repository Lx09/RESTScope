"""Own mutable App-lifetime request-generation state.

The store receives a parsed OpenAPI document during App initialization and
creates one default Generator configuration for every operation. Callers can
freeze an immutable revision for Batch execution or atomically replace one
operation after a Parameter Patch has been revalidated. No Generator,
Constraint, Patch, sample, or revision history is persisted.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
from threading import RLock
from typing import Iterator, Literal, TypeVar

from restscope.target_api.media_type import normalize_media_type
from restscope.openapi_parser import OpenAPISpecIR
from restscope.operation_references.response import ResponseSourceCoordinate

from .models import (
    ArrayGenerator,
    BooleanGenerator,
    ChoiceGenerator,
    ConstantGenerator,
    FormatGenerator,
    InputGeneratorConfig,
    InputGeneratorPatch,
    InputNodeSnapshot,
    GeneratorDisabledReason,
    IntegerRangeGenerator,
    NumberRangeGenerator,
    ObjectGenerator,
    OperationGeneratorConfig,
    RandomStringGenerator,
    RegexGenerator,
    ResourceIdentifierGenerator,
    ResponseValueGenerator,
    RequestBodyGenerator,
    VariantGenerator,
)
from .snapshot import build_initial_catalog
from .constraints import OperationConstraintRecord


_T = TypeVar("_T")


class ReferenceValueBinding(ResponseSourceCoordinate):
    """Bind one Generator input to its exact persisted response source.

    ``resource_name`` is present only for a resource identity source.  All
    other coordinates are shared by resource and generic value reuse.  These
    immutable facts participate in the Generation State digest; response
    values remain in observations until a reader requests them.
    """

    input_node_id: str
    kind: Literal["resource_identifier", "response_value"]
    resource_name: str | None = None


@dataclass(frozen=True, slots=True)
class RequestGenerationState:
    """Freeze one complete operation revision for readers and Batch execution.

    ``config`` contains every current Generator and the immutable OpenAPI input
    snapshot. ``constraints`` contains every active normalized cross-input
    relationship. Digests identify exact content without exposing internal
    input-node identifiers in model-facing Tool results.
    """

    config: OperationGeneratorConfig
    constraints: tuple[OperationConstraintRecord, ...]
    revision: int
    state_digest: str
    last_applied_validation_digest: str | None
    reference_bindings: tuple[ReferenceValueBinding, ...] = ()


@dataclass(slots=True)
class _MutableOperationState:
    """Keep one operation's replaceable content behind its private lock."""

    config: OperationGeneratorConfig
    constraints: tuple[OperationConstraintRecord, ...]
    revision: int
    state_digest: str
    last_applied_validation_digest: str | None
    reference_bindings: tuple[ReferenceValueBinding, ...]
    lock: RLock


class _OperationReplacement:
    """Publish one reversible in-memory replacement while its lock is held."""

    def __init__(self, state: _MutableOperationState) -> None:
        self._state = state
        self.before = _freeze_state(state)
        self._published = False

    def publish(
        self,
        *,
        config: OperationGeneratorConfig,
        constraints: Sequence[OperationConstraintRecord],
        reference_bindings: Sequence[ReferenceValueBinding],
        validation_digest: str,
    ) -> RequestGenerationState:
        """Replace live content, retaining enough state for automatic rollback."""
        next_config = config.model_copy(deep=True)
        next_constraints = tuple(item.model_copy(deep=True) for item in constraints)
        next_bindings = tuple(reference_bindings)
        next_digest = generation_state_digest(
            next_config,
            next_constraints,
            next_bindings,
        )
        if next_digest == self._state.state_digest:
            raise GeneratorConfigError(
                "generator_patch_no_change",
                "The validated Patch does not change Generator, Constraint, or reference state",
            )
        self._state.config = next_config
        self._state.constraints = next_constraints
        self._state.reference_bindings = next_bindings
        self._state.revision += 1
        self._state.state_digest = next_digest
        self._state.last_applied_validation_digest = validation_digest
        self._published = True
        return _freeze_state(self._state)

    def rollback(self) -> None:
        """Restore the exact prior revision after a later durable commit fails."""
        if not self._published:
            return
        self._state.config = self.before.config.model_copy(deep=True)
        self._state.constraints = tuple(
            item.model_copy(deep=True) for item in self.before.constraints
        )
        self._state.reference_bindings = self.before.reference_bindings
        self._state.revision = self.before.revision
        self._state.state_digest = self.before.state_digest
        self._state.last_applied_validation_digest = (
            self.before.last_applied_validation_digest
        )


class GeneratorConfigError(ValueError):
    """Base error with a stable code for generation and execution boundaries."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        input_node_ids: Sequence[str] = (),
    ) -> None:
        super().__init__(message)
        self.code = code
        self.input_node_ids = tuple(input_node_ids)


class GeneratorConfigStateConflict(GeneratorConfigError):
    """Report that stored Generator content changed before Patch commit."""

    def __init__(self) -> None:
        super().__init__(
            "request_generation_state_conflict",
            "Generator configuration changed while the Patch was being prepared",
        )


class RequestGenerationConfigStore:
    """Expose immutable snapshots and atomic replacements of current state.

    The outer lock protects initialization and the operation map. Each
    operation has a separate re-entrant lock, so unrelated operations can run
    concurrently while a Batch or Patch obtains a coherent snapshot.
    """

    def __init__(self) -> None:
        self._states: dict[str, _MutableOperationState] = {}
        self._catalog_lock = RLock()

    def get_operation(self, operation_key: str) -> OperationGeneratorConfig | None:
        """Return a detached current Generator configuration, or ``None``."""
        state = self.get_state(operation_key)
        return state.config if state is not None else None

    def get_state(self, operation_key: str) -> RequestGenerationState | None:
        """Return one detached, internally consistent operation snapshot."""
        with self._catalog_lock:
            state = self._states.get(operation_key)
        if state is None:
            return None
        with state.lock:
            return _freeze_state(state)

    def initialize_once(self, ir: OpenAPISpecIR) -> bool:
        """Create revision-zero state once from the App's current OpenAPI IR."""
        records = [_validate_initial_record(item) for item in build_initial_catalog(ir)]
        with self._catalog_lock:
            if self._states:
                return False
            self._states = {
                item.operation_key: _new_operation_state(item)
                for item in records
            }
        return True

    def require_operation(self, operation_key: str) -> OperationGeneratorConfig:
        """Return initialized Generator state for one operation or raise a stable unknown-operation error."""
        current = self._require_existing(operation_key)
        if not current.enabled:
            reasons = [reason.code for reason in current.disabled_reasons]
            raise GeneratorConfigError(
                "generator_operation_unsupported",
                f"Generator operation is disabled: {reasons}",
            )
        return current

    def require_state(self, operation_key: str) -> RequestGenerationState:
        """Return a detached complete state or raise the stable unknown error."""
        state = self.get_state(operation_key)
        if state is None:
            raise GeneratorConfigError(
                "generator_config_not_found",
                f"No generator configuration exists for {operation_key}",
            )
        if not state.config.enabled:
            reasons = [reason.code for reason in state.config.disabled_reasons]
            raise GeneratorConfigError(
                "generator_operation_unsupported",
                f"Generator operation is disabled: {reasons}",
            )
        return state

    @contextmanager
    def _replacement_transaction(
        self,
        *,
        operation_key: str,
        expected_revision: int,
    ) -> Iterator[_OperationReplacement]:
        """Hold one operation lock across staging, publication, and commit.

        This private seam lets the Parameter Patch runtime coordinate its
        in-memory revision with a separately owned database transaction.  If
        anything fails after publication, the old in-memory state is restored
        before the lock becomes visible to Batch readers.
        """
        with self._catalog_lock:
            state = self._states.get(operation_key)
        if state is None:
            raise GeneratorConfigError(
                "generator_config_not_found",
                f"No generator configuration exists for {operation_key}",
            )
        with state.lock:
            if state.revision != expected_revision:
                raise GeneratorConfigStateConflict()
            replacement = _OperationReplacement(state)
            try:
                yield replacement
            except BaseException:
                replacement.rollback()
                raise

    def _snapshot_with(
        self,
        operation_key: str,
        capture: Callable[[RequestGenerationState], _T],
    ) -> tuple[RequestGenerationState, _T]:
        """Capture related volatile evidence under the operation read lock.

        Batch execution uses this seam to freeze reference values together with
        the revision whose Generators name them.  ``capture`` must perform only
        bounded reads and must not call back into Patch mutation.
        """
        with self._catalog_lock:
            state = self._states.get(operation_key)
        if state is None:
            raise GeneratorConfigError(
                "generator_config_not_found",
                f"No generator configuration exists for {operation_key}",
            )
        with state.lock:
            frozen = _freeze_state(state)
            return frozen, capture(frozen)

    def _require_existing(self, operation_key: str) -> OperationGeneratorConfig:
        current = self.get_operation(operation_key)
        if current is None:
            raise GeneratorConfigError(
                "generator_config_not_found",
                f"No generator configuration exists for {operation_key}",
            )
        return current


def generation_state_digest(
    config: OperationGeneratorConfig,
    constraints: Sequence[OperationConstraintRecord],
    reference_bindings: Sequence[ReferenceValueBinding] = (),
) -> str:
    """Return a stable SHA-256 digest for all mutable generation content."""
    payload = {
        "operation_key": config.operation_key,
        "active_media_type": config.active_media_type,
        "configs": [item.model_dump(mode="json") for item in config.configs],
        "constraints": [item.model_dump(mode="json") for item in constraints],
        "reference_bindings": [
            item.model_dump(mode="json") for item in reference_bindings
        ],
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _new_operation_state(config: OperationGeneratorConfig) -> _MutableOperationState:
    """Build revision-zero mutable state from one validated baseline."""
    detached = config.model_copy(deep=True)
    return _MutableOperationState(
        config=detached,
        constraints=(),
        revision=0,
        state_digest=generation_state_digest(detached, ()),
        last_applied_validation_digest=None,
        reference_bindings=(),
        lock=RLock(),
    )


def _freeze_state(state: _MutableOperationState) -> RequestGenerationState:
    """Detach mutable containers before exposing a revision to a caller."""
    return RequestGenerationState(
        config=state.config.model_copy(deep=True),
        constraints=tuple(item.model_copy(deep=True) for item in state.constraints),
        revision=state.revision,
        state_digest=state.state_digest,
        last_applied_validation_digest=state.last_applied_validation_digest,
        reference_bindings=state.reference_bindings,
    )

def _apply_patches(
    current: OperationGeneratorConfig,
    updates: Sequence[InputGeneratorPatch],
) -> tuple[list[InputGeneratorConfig], set[str]]:
    """Apply complete Generator replacements to a copied operation state before validation and persistence."""
    patches = expand_generator_patch_presence(current, updates)
    if not patches:
        raise GeneratorConfigError(
            "generator_patch_empty",
            "At least one generator patch is required",
        )
    counts = Counter(item.input_node_id for item in patches)
    duplicates = sorted(
        node_id for node_id, count in counts.items() if count > 1
    )
    current_by_id = {item.input_node_id: item for item in current.configs}
    unknown = sorted(set(counts) - set(current_by_id))
    if duplicates or unknown:
        raise GeneratorConfigError(
            "generator_patch_invalid_nodes",
            f"Invalid generator patch nodes; unknown={unknown}, duplicates={duplicates}",
        )
    for patch in patches:
        existing = current_by_id[patch.input_node_id]
        payload = existing.model_dump()
        if patch.inclusion_probability is not None:
            payload["inclusion_probability"] = patch.inclusion_probability
        if patch.strategy is not None:
            payload["strategy"] = patch.strategy
        current_by_id[patch.input_node_id] = InputGeneratorConfig.model_validate(
            payload
        )
    return (
        [
            current_by_id[item.input_node_id]
            for item in current.configs
        ],
        set(counts),
    )


def expand_generator_patch_presence(
    current: OperationGeneratorConfig,
    updates: Sequence[InputGeneratorPatch],
) -> list[InputGeneratorPatch]:
    """Expand explicit mandatory descendants into mandatory ancestor updates.

    Request generation samples every configured node independently.  Therefore
    setting a nested leaf's inclusion probability to one cannot guarantee the
    leaf is present unless every ancestor is also present.  This helper defines
    that meaning only for an explicit Patch; it deliberately does not change
    the baseline semantics of required children inside optional containers.
    """

    patches = [InputGeneratorPatch.model_validate(update) for update in updates]
    if not patches:
        raise GeneratorConfigError(
            "generator_patch_empty",
            "At least one generator patch is required",
        )
    counts = Counter(item.input_node_id for item in patches)
    duplicates = sorted(
        node_id for node_id, count in counts.items() if count > 1
    )
    current_by_id = {item.input_node_id: item for item in current.configs}
    unknown = sorted(set(counts) - set(current_by_id))
    if duplicates or unknown:
        raise GeneratorConfigError(
            "generator_patch_invalid_nodes",
            f"Invalid generator patch nodes; unknown={unknown}, duplicates={duplicates}",
        )

    nodes_by_id = {
        item.input_node_id: item for item in current.snapshot.input_nodes
    }
    explicit_by_id = {item.input_node_id: item for item in patches}
    required_ancestor_ids: list[str] = []
    for patch in patches:
        if patch.inclusion_probability != 1:
            continue
        node = nodes_by_id.get(patch.input_node_id)
        while node is not None and node.parent_node_id is not None:
            ancestor_id = node.parent_node_id
            explicit_ancestor = explicit_by_id.get(ancestor_id)
            if (
                explicit_ancestor is not None
                and explicit_ancestor.inclusion_probability is not None
                and explicit_ancestor.inclusion_probability < 1
            ):
                raise GeneratorConfigError(
                    "presence_closure_conflict",
                    "A descendant cannot be mandatory while an explicitly "
                    f"patched ancestor is optional: {ancestor_id}",
                    input_node_ids=(ancestor_id, patch.input_node_id),
                )
            if ancestor_id not in required_ancestor_ids:
                required_ancestor_ids.append(ancestor_id)
            node = nodes_by_id.get(ancestor_id)

    expanded_by_id = dict(explicit_by_id)
    for ancestor_id in required_ancestor_ids:
        explicit = expanded_by_id.get(ancestor_id)
        if explicit is not None:
            if (
                explicit.inclusion_probability == 1
                or (
                    explicit.inclusion_probability is None
                    and current_by_id[ancestor_id].inclusion_probability == 1
                )
            ):
                continue
            expanded_by_id[ancestor_id] = explicit.model_copy(
                update={"inclusion_probability": 1}
            )
            continue
        if current_by_id[ancestor_id].inclusion_probability == 1:
            continue
        expanded_by_id[ancestor_id] = InputGeneratorPatch(
            input_node_id=ancestor_id,
            inclusion_probability=1,
        )

    original_ids = [item.input_node_id for item in patches]
    synthetic_ids = [
        node_id
        for node_id in required_ancestor_ids
        if node_id not in original_ids
        and node_id in expanded_by_id
    ]
    return [
        expanded_by_id[node_id]
        for node_id in [*original_ids, *synthetic_ids]
    ]


def preview_generator_patch(
    current: OperationGeneratorConfig,
    updates: Sequence[InputGeneratorPatch],
) -> OperationGeneratorConfig:
    """Apply and validate a generator patch without catalog persistence."""

    if not updates:
        return current
    updated, _ = _apply_patches(current, updates)
    _validate_configs(
        current,
        media_type=current.active_media_type,
        configs=updated,
        enforce_schema=False,
    )
    return current.model_copy(update={"configs": updated})


def prepare_accepted_generator_patch(
    current: OperationGeneratorConfig,
    updates: Sequence[InputGeneratorPatch],
) -> OperationGeneratorConfig:
    """Build the accepted current Generator state without writing a database.

    Patch validation uses this pure step before opening its atomic state update
    transaction.  It applies presence closure, validates every resulting input
    config, and clears only recoverable disabled reasons owned by patched
    inputs.  The caller remains responsible for a content-compare write.
    """
    updated, patched_node_ids = _apply_patches(current, updates)
    _validate_configs(
        current,
        media_type=current.active_media_type,
        configs=updated,
        enforce_schema=False,
    )
    del patched_node_ids
    return rebuild_generator_config(current, updated)


def rebuild_generator_config(
    baseline: OperationGeneratorConfig,
    configs: list[InputGeneratorConfig],
) -> OperationGeneratorConfig:
    """Derive enabled state from the immutable snapshot and current inputs.

    Recoverable default-generator failures are deliberately recomputed instead
    of persisted.  A successful Patch can therefore make an operation usable
    without an operation-level state row or historical revision.
    """

    contract_reasons = _contract_disabled_reasons(
        baseline,
        baseline.active_media_type,
    )
    baseline_by_id = {
        item.input_node_id: item for item in baseline.configs
    }
    changed_input_ids = {
        item.input_node_id
        for item in configs
        if baseline_by_id.get(item.input_node_id) != item
    }
    # Default derivation failures are attached to their input.  A later
    # accepted Patch clears only failures for inputs whose current content is
    # different from the original baseline; failures on untouched inputs
    # remain visible and keep the operation disabled.
    remaining_recoverable = [
        reason
        for reason in baseline.disabled_reasons
        if reason.recoverable
        and not _changed_input_can_repair_reason(
            baseline,
            reason=reason,
            changed_input_ids=changed_input_ids,
        )
    ]
    reasons = [*contract_reasons, *remaining_recoverable]
    candidate = baseline.model_copy(
        update={
            "configs": configs,
            "enabled": not reasons,
            "disabled_reasons": reasons,
        }
    )
    _validate_configs(
        candidate,
        media_type=candidate.active_media_type,
        configs=candidate.configs,
        enforce_schema=False,
    )
    return candidate


def _changed_input_can_repair_reason(
    baseline: OperationGeneratorConfig,
    *,
    reason: GeneratorDisabledReason,
    changed_input_ids: set[str],
) -> bool:
    """Return whether a current change touches the reason's input subtree.

    Some derivation failures are attributed to a container even though a child
    Generator controls whether the container satisfies cardinality.  Walking
    parent links lets a changed descendant clear that container-level failure
    without clearing unrelated input failures.
    """

    if not changed_input_ids:
        return False
    if reason.input_node_id is None:
        return True
    if reason.input_node_id in changed_input_ids:
        return True
    nodes = {
        item.input_node_id: item for item in baseline.snapshot.input_nodes
    }
    for changed_id in changed_input_ids:
        node = nodes.get(changed_id)
        while node is not None and node.parent_node_id is not None:
            if node.parent_node_id == reason.input_node_id:
                return True
            node = nodes.get(node.parent_node_id)
    return False


def _validate_initial_record(
    record: OperationGeneratorConfig,
) -> OperationGeneratorConfig:
    """
    Validate initial record for deterministic request generation, constraint solving,
    and execution.
    """
    try:
        _validate_configs(
            record,
            media_type=record.active_media_type,
            configs=record.configs,
            enforce_schema=True,
        )
    except GeneratorConfigError as exc:
        node_ids: tuple[str | None, ...] = (
            exc.input_node_ids
            if exc.input_node_ids
            else (None,)
        )
        reasons = [
            *record.disabled_reasons,
            *[
                GeneratorDisabledReason(
                    code="default_generator_unavailable",
                    message=f"Default generator set is invalid: {exc}",
                    recoverable=True,
                    input_node_id=node_id,
                )
                for node_id in node_ids
            ],
        ]
        unique = {
            (
                item.code,
                item.message,
                item.recoverable,
                item.input_node_id,
            ): item
            for item in reasons
        }
        return record.model_copy(
            update={
                "enabled": False,
                "disabled_reasons": list(unique.values()),
            }
        )
    return record


def _validate_configs(
    current: OperationGeneratorConfig,
    *,
    media_type: str | None,
    configs: list[InputGeneratorConfig],
    enforce_schema: bool,
) -> None:
    """Verify every Generator matches its frozen input node and every container has complete child configuration."""
    nodes = {
        node.input_node_id: node
        for node in current.snapshot.input_nodes
    }
    counts = Counter(config.input_node_id for config in configs)
    duplicates = sorted(node_id for node_id, count in counts.items() if count > 1)
    supplied_ids = set(counts)
    missing = sorted(set(nodes) - supplied_ids)
    extra = sorted(supplied_ids - set(nodes))
    if duplicates or missing or extra:
        raise GeneratorConfigError(
            "generator_config_incomplete",
            f"Invalid input node set; missing={missing}, extra={extra}, duplicates={duplicates}",
        )
    invalid_inclusion = sorted(
        config.input_node_id
        for config in configs
        if nodes[config.input_node_id].required
        and config.inclusion_probability != 1.0
    )
    if invalid_inclusion:
        raise GeneratorConfigError(
            "generator_config_invalid_inclusion",
            "Required or structural input nodes must use inclusion_probability=1.0: "
            f"{invalid_inclusion}",
        )
    selected_node_ids = _selected_node_ids(current, media_type)
    configs_by_id = {item.input_node_id: item for item in configs}
    selected_node_ids = _effective_node_ids(
        selected_node_ids,
        nodes=nodes,
        configs=configs_by_id,
    )
    incompatible = {
        config.input_node_id
        for config in configs
        if config.input_node_id in selected_node_ids
        if not (
            _strategy_matches_node(nodes[config.input_node_id], config)
            if enforce_schema
            else _strategy_can_build_node(nodes[config.input_node_id], config)
        )
    }
    incompatible.update(
        _media_body_strategy_errors(
            current=current,
            media_type=media_type,
            configs=configs_by_id,
        )
    )
    if enforce_schema:
        incompatible.update(
            _container_configuration_errors(
                nodes=nodes,
                configs=configs_by_id,
                selected_node_ids=selected_node_ids,
            )
        )
    if incompatible:
        raise GeneratorConfigError(
            "generator_config_incompatible_strategy",
            "Generator strategies do not match frozen input nodes: "
            f"{sorted(incompatible)}",
            input_node_ids=sorted(incompatible),
        )


def _effective_node_ids(
    selected_node_ids: set[str],
    *,
    nodes: dict[str, InputNodeSnapshot],
    configs: dict[str, InputGeneratorConfig],
) -> set[str]:
    effective: set[str] = set()
    for node_id in selected_node_ids:
        parent_id = nodes[node_id].parent_node_id
        shadowed = False
        while parent_id is not None:
            parent_strategy = configs[parent_id].strategy
            if isinstance(parent_strategy, ConstantGenerator | ChoiceGenerator):
                shadowed = True
                break
            parent_id = nodes[parent_id].parent_node_id
        if not shadowed:
            effective.add(node_id)
    return effective


def _strategy_can_build_node(
    node: InputNodeSnapshot,
    config: InputGeneratorConfig,
) -> bool:
    """Return whether a Generator strategy can produce values compatible with one input node snapshot."""
    strategy = config.strategy
    if node.node_kind == "request_body":
        return isinstance(strategy, RequestBodyGenerator)
    if isinstance(strategy, RequestBodyGenerator):
        return False
    if isinstance(
        strategy,
        ResourceIdentifierGenerator | ResponseValueGenerator,
    ):
        schema = node.schema_contract
        return (
            schema is not None
            and not schema.properties
            and schema.items is None
            and not schema.all_of
            and not schema.any_of
            and not schema.one_of
            and not _has_type(schema, "object")
            and not _has_type(schema, "array")
        )
    if isinstance(strategy, ArrayGenerator):
        return node.schema_contract is not None and node.schema_contract.items is not None
    if isinstance(strategy, VariantGenerator):
        if node.schema_contract is None:
            return False
        branches = node.schema_contract.one_of or node.schema_contract.any_of
        return bool(branches) and len(strategy.branch_weights) == len(branches)
    return True


def _media_body_strategy_errors(
    *,
    current: OperationGeneratorConfig,
    media_type: str | None,
    configs: dict[str, InputGeneratorConfig],
) -> set[str]:
    """Report request-body Generator mismatches for the selected media type."""
    if media_type is None:
        return set()
    media_node_id = current.snapshot.media_type_node_ids.get(media_type)
    if media_node_id is None:
        return set()
    strategy = configs[media_node_id].strategy
    normalized = normalize_media_type(media_type) or ""
    if normalized.startswith("text/"):
        if isinstance(strategy, ConstantGenerator):
            return set() if isinstance(strategy.value, str) else {media_node_id}
        if isinstance(strategy, ChoiceGenerator):
            return (
                set()
                if all(isinstance(value, str) for value in strategy.values)
                else {media_node_id}
            )
        return (
            set()
            if isinstance(
                strategy,
                RandomStringGenerator | RegexGenerator | FormatGenerator,
            )
            else {media_node_id}
        )
    if normalized == "application/x-www-form-urlencoded":
        if isinstance(strategy, ConstantGenerator):
            return set() if isinstance(strategy.value, dict) else {media_node_id}
        if isinstance(strategy, ChoiceGenerator):
            return (
                set()
                if all(isinstance(value, dict) for value in strategy.values)
                else {media_node_id}
            )
        return set() if isinstance(strategy, ObjectGenerator) else {media_node_id}
    return set()


def _container_configuration_errors(
    *,
    nodes: dict[str, InputNodeSnapshot],
    configs: dict[str, InputGeneratorConfig],
    selected_node_ids: set[str],
) -> set[str]:
    """Report missing or incompatible child Generators inside object and array containers."""
    invalid: set[str] = set()
    for node in nodes.values():
        if node.input_node_id not in selected_node_ids:
            continue
        schema = node.schema_contract
        if schema is None or not (
            _has_type(schema, "object") or schema.properties
        ):
            continue
        if isinstance(
            configs[node.input_node_id].strategy,
            ConstantGenerator | ChoiceGenerator,
        ):
            continue
        property_children = [
            child
            for child in nodes.values()
            if child.parent_node_id == node.input_node_id
            and child.canonical_path.startswith(
                f"{node.canonical_path}/properties/"
            )
        ]
        guaranteed = sum(
            configs[child.input_node_id].inclusion_probability == 1
            for child in property_children
        )
        possible = sum(
            configs[child.input_node_id].inclusion_probability > 0
            for child in property_children
        )
        if (
            schema.min_properties is not None
            and guaranteed < schema.min_properties
        ) or (
            schema.max_properties is not None
            and possible > schema.max_properties
        ):
            invalid.add(node.input_node_id)
    return invalid


def _selected_node_ids(
    current: OperationGeneratorConfig,
    active_media_type: str | None,
) -> set[str]:
    """Collect every input node selected by the current Generator tree."""
    snapshot = current.snapshot
    active_root_id = (
        snapshot.media_type_node_ids.get(active_media_type)
        if active_media_type is not None
        else None
    )
    active_root = next(
        (
            node
            for node in snapshot.input_nodes
            if node.input_node_id == active_root_id
        ),
        None,
    )
    return {
        node.input_node_id
        for node in snapshot.input_nodes
        if not node.canonical_path.startswith("body/")
        or (
            active_root is not None
            and (
                node.input_node_id == active_root.input_node_id
                or node.canonical_path.startswith(
                    f"{active_root.canonical_path}/"
                )
            )
        )
    }


def _contract_disabled_reasons(
    current: OperationGeneratorConfig,
    active_media_type: str | None,
) -> list[GeneratorDisabledReason]:
    snapshot = current.snapshot
    selected_node_ids = _selected_node_ids(current, active_media_type)
    reasons = [
        GeneratorDisabledReason(
            code="request_parameter_unsupported",
            message=message,
        )
        for node_id, message in snapshot.unsupported_parameter_nodes.items()
        if node_id in selected_node_ids
    ]
    if snapshot.request_body_node_id is not None and active_media_type is None:
        reasons.append(
            GeneratorDisabledReason(
                code="request_body_media_type_unsupported",
                message="Operation has no supported request body media type",
            )
        )
    return reasons


def _strategy_matches_node(
    node: InputNodeSnapshot,
    config: InputGeneratorConfig,
) -> bool:
    """Return whether a persisted strategy still matches the input node kind and Schema."""
    strategy = config.strategy
    if node.node_kind == "request_body":
        return isinstance(strategy, RequestBodyGenerator)
    schema = node.schema_contract
    if schema is None:
        return False
    from .generation import generate_strategy_value, schema_matches

    if isinstance(strategy, ConstantGenerator):
        return True
    if isinstance(strategy, ChoiceGenerator):
        return True
    if schema.one_of or schema.any_of:
        branch_count = len(schema.one_of or schema.any_of)
        return (
            isinstance(strategy, VariantGenerator)
            and len(strategy.branch_weights) == branch_count
        )
    if schema.all_of:
        return isinstance(strategy, ObjectGenerator)
    if _has_type(schema, "object") or schema.properties:
        return isinstance(strategy, ObjectGenerator)
    if _has_type(schema, "array") or schema.items is not None:
        return (
            isinstance(strategy, ArrayGenerator)
            and (schema.min_items is None or strategy.min_items >= schema.min_items)
            and (schema.max_items is None or strategy.max_items <= schema.max_items)
            and (not schema.unique_items or strategy.max_items <= 1)
        )
    if isinstance(strategy, IntegerRangeGenerator):
        if not (
            schema_matches(schema, strategy.minimum)
            and schema_matches(schema, strategy.maximum)
        ):
            return False
        discrete_constraint = (
            schema.multiple_of is not None
            or schema.enum is not None
            or schema.has_const
        )
        if discrete_constraint and strategy.minimum != strategy.maximum:
            span = strategy.maximum - strategy.minimum
            return span <= 10_000 and all(
                schema_matches(schema, value)
                for value in range(strategy.minimum, strategy.maximum + 1)
            )
        return True
    if isinstance(strategy, NumberRangeGenerator):
        return (
            schema_matches(schema, strategy.minimum)
            and schema_matches(schema, strategy.maximum)
            and (
                (
                    schema.multiple_of is None
                    and schema.enum is None
                    and not schema.has_const
                )
                or strategy.minimum == strategy.maximum
            )
        )
    if isinstance(strategy, BooleanGenerator):
        candidates = []
        if strategy.true_probability > 0:
            candidates.append(True)
        if strategy.true_probability < 1:
            candidates.append(False)
        return all(schema_matches(schema, value) for value in candidates)
    if isinstance(strategy, RandomStringGenerator):
        if (
            schema.pattern is not None
            or schema.format
            or schema.has_const
            or schema.enum
        ):
            return False
        return all(
            schema_matches(schema, value)
            for value in (
                strategy.alphabet[:1] * strategy.min_length,
                strategy.alphabet[:1] * strategy.max_length,
            )
        )
    if isinstance(strategy, RegexGenerator):
        if (
            schema.pattern is None
            or schema.pattern != strategy.pattern
            or schema.format is not None
            or schema.has_const
            or schema.enum
            or not (_has_type(schema, "string") or schema.type is None)
            or (
                schema.min_length is not None
                and strategy.min_length < schema.min_length
            )
            or (
                schema.max_length is not None
                and strategy.max_length > schema.max_length
            )
        ):
            return False
        try:
            return all(
                schema_matches(
                    schema,
                    generate_strategy_value(strategy, seed=seed),
                )
                for seed in (0, 1)
            )
        except ValueError:
            return False
    if isinstance(strategy, FormatGenerator):
        if schema.pattern is not None or schema.has_const or schema.enum:
            return False
        if schema.format not in {None, strategy.format}:
            return False
        return all(
            schema_matches(
                schema,
                generate_strategy_value(strategy, seed=seed),
            )
            for seed in (0, 1)
        )
    return False


def _has_type(schema, expected: str) -> bool:
    return (
        expected in schema.type
        if isinstance(schema.type, list)
        else schema.type == expected
    )
