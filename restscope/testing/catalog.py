"""Validated, versioned generator catalog independent from later OpenAPI IRs."""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence

from restscope.openapi_parser import OpenAPISpecIR

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
from .ports import GeneratorConfigConcurrentWrite, GeneratorConfigUnitOfWorkFactory
from .snapshot import build_initial_catalog


class GeneratorConfigError(ValueError):
    """Base error with a stable code for Smoke and execution boundaries."""

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


class GeneratorConfigRevisionConflict(GeneratorConfigError):
    """
    Coordinate generator config revision conflict behavior for deterministic request
    generation, constraint solving, and execution.

    Read the public methods as the supported lifecycle and treat underscore-prefixed
    helpers as internal implementation details.
    """
    def __init__(self, *, expected: int, actual: int | None) -> None:
        actual_text = str(actual) if actual is not None else "changed concurrently"
        super().__init__(
            "generator_config_revision_conflict",
            f"Generator configuration revision conflict: expected {expected}, actual {actual_text}",
        )


class GeneratorConfigCatalog:
    """Initialize frozen configs and append directly accepted revisions."""

    def __init__(self, unit_of_work_factory: GeneratorConfigUnitOfWorkFactory) -> None:
        self.unit_of_work_factory = unit_of_work_factory

    def get_operation(self, operation_key: str) -> OperationGeneratorConfig | None:
        """
        Return operation for deterministic request generation, constraint solving, and
        execution.

        The class owns any required collaborators or state; arguments supply only the
        data needed for this call.
        """
        with self.unit_of_work_factory() as uow:
            return uow.generator_configs.get(operation_key)

    def initialize_once(self, ir: OpenAPISpecIR) -> bool:
        """Persist the first IR-derived generator catalog and never resync it."""

        with self.unit_of_work_factory() as uow:
            if uow.generator_configs.is_initialized():
                return False
            records = [
                _validate_initial_record(record)
                for record in build_initial_catalog(ir)
            ]
            try:
                uow.generator_configs.initialize(records)
            except GeneratorConfigConcurrentWrite:
                uow.rollback()
                return False
            uow.commit()
            return True

    def apply_accepted_patch(
        self,
        *,
        operation_key: str,
        expected_revision: int,
        updates: Sequence[InputGeneratorPatch],
    ) -> OperationGeneratorConfig:
        """Validate and persist one directly accepted Generator revision.

        This generic catalog operation is used by non-Smoke callers and focused
        tests.  Operation Smoke uses its wider atomic Unit of Work so this same
        Generator write commits together with Investigation memory.
        """
        current = self._require_existing(operation_key)
        if current.revision != expected_revision:
            raise GeneratorConfigRevisionConflict(
                expected=expected_revision,
                actual=current.revision,
            )
        accepted = prepare_accepted_generator_patch(current, updates)
        with self.unit_of_work_factory() as uow:
            latest = uow.generator_configs.get(operation_key)
            actual_revision = latest.revision if latest is not None else 0
            if actual_revision != expected_revision:
                raise GeneratorConfigRevisionConflict(
                    expected=expected_revision,
                    actual=actual_revision,
                )
            try:
                persisted = uow.generator_configs.replace(
                    operation_key=operation_key,
                    expected_revision=expected_revision,
                    revision=accepted.revision,
                    snapshot=accepted.snapshot.model_dump(mode="json"),
                    enabled=accepted.enabled,
                    disabled_reasons=[
                        item.model_dump(mode="json")
                        for item in accepted.disabled_reasons
                    ],
                    active_media_type=accepted.active_media_type,
                    configs=accepted.configs,
                )
            except GeneratorConfigConcurrentWrite as exc:
                raise GeneratorConfigRevisionConflict(
                    expected=expected_revision,
                    actual=None,
                ) from exc
            uow.commit()
            return persisted

    def require_operation(self, operation_key: str) -> OperationGeneratorConfig:
        """
        Handle require operation as part of deterministic request generation, constraint
        solving, and execution.

        The class owns any required collaborators or state; arguments supply only the
        data needed for this call.
        """
        current = self._require_existing(operation_key)
        if not current.enabled:
            reasons = [reason.code for reason in current.disabled_reasons]
            raise GeneratorConfigError(
                "generator_operation_unsupported",
                f"Generator operation is disabled: {reasons}",
            )
        return current

    def _require_existing(self, operation_key: str) -> OperationGeneratorConfig:
        current = self.get_operation(operation_key)
        if current is None:
            raise GeneratorConfigError(
                "generator_config_not_found",
                f"No generator configuration exists for {operation_key}",
            )
        return current

def _apply_patches(
    current: OperationGeneratorConfig,
    updates: Sequence[InputGeneratorPatch],
) -> tuple[list[InputGeneratorConfig], set[str]]:
    """
    Apply patches for deterministic request generation, constraint solving, and
    execution.

    This private helper keeps one transformation or policy decision explicit so the
    surrounding orchestration remains readable.
    """
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
    """Build the next directly accepted revision without writing a database.

    Operation Smoke uses this pure step before opening its atomic persistence
    transaction.  It applies presence closure, validates every resulting input
    config, and clears only recoverable disabled reasons owned by patched
    inputs.  The caller remains responsible for the revision-lock write.
    """
    updated, patched_node_ids = _apply_patches(current, updates)
    _validate_configs(
        current,
        media_type=current.active_media_type,
        configs=updated,
        enforce_schema=False,
    )
    remaining_reasons = _contract_disabled_reasons(
        current,
        current.active_media_type,
    )
    remaining_reasons.extend(
        reason
        for reason in current.disabled_reasons
        if reason.recoverable
        and reason.input_node_id not in patched_node_ids
    )
    # The same condition can come from the frozen contract and the previous
    # catalog.  Keep one readable reason rather than accumulating duplicates.
    unique_reasons = list(
        {
            (
                item.code,
                item.message,
                item.recoverable,
                item.input_node_id,
            ): item
            for item in remaining_reasons
        }.values()
    )
    return current.model_copy(
        update={
            "revision": current.revision + 1,
            "configs": updated,
            "enabled": not unique_reasons,
            "disabled_reasons": unique_reasons,
        }
    )


def _validate_initial_record(
    record: OperationGeneratorConfig,
) -> OperationGeneratorConfig:
    """
    Validate initial record for deterministic request generation, constraint solving,
    and execution.

    This private helper keeps one transformation or policy decision explicit so the
    surrounding orchestration remains readable.
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
    """
    Validate configs for deterministic request generation, constraint solving, and
    execution.

    This private helper keeps one transformation or policy decision explicit so the
    surrounding orchestration remains readable.
    """
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
    """
    Handle strategy can build node as part of deterministic request generation,
    constraint solving, and execution.

    This private helper keeps one transformation or policy decision explicit so the
    surrounding orchestration remains readable.
    """
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
    """
    Handle media body strategy errors as part of deterministic request generation,
    constraint solving, and execution.

    This private helper keeps one transformation or policy decision explicit so the
    surrounding orchestration remains readable.
    """
    if media_type is None:
        return set()
    media_node_id = current.snapshot.media_type_node_ids.get(media_type)
    if media_node_id is None:
        return set()
    strategy = configs[media_node_id].strategy
    normalized = media_type.split(";", 1)[0].strip().lower()
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
    """
    Handle container configuration errors as part of deterministic request generation,
    constraint solving, and execution.

    This private helper keeps one transformation or policy decision explicit so the
    surrounding orchestration remains readable.
    """
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
    """
    Handle selected node ids as part of deterministic request generation, constraint
    solving, and execution.

    This private helper keeps one transformation or policy decision explicit so the
    surrounding orchestration remains readable.
    """
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
    """
    Handle strategy matches node as part of deterministic request generation, constraint
    solving, and execution.

    This private helper keeps one transformation or policy decision explicit so the
    surrounding orchestration remains readable.
    """
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
