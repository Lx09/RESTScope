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
    GeneratorConfigRevision,
    InputGeneratorConfig,
    InputGeneratorPatch,
    InputNodeSnapshot,
    GeneratorDisabledReason,
    IntegerRangeGenerator,
    NumberRangeGenerator,
    ObjectGenerator,
    OperationGeneratorConfig,
    RandomStringGenerator,
    ResourceIdentifierGenerator,
    ResponseValueGenerator,
    RequestBodyGenerator,
    VariantGenerator,
)
from .ports import GeneratorConfigConcurrentWrite, GeneratorConfigUnitOfWorkFactory
from .snapshot import build_initial_catalog


class GeneratorConfigError(ValueError):
    """Base error with a stable code suitable for tool results."""

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
    def __init__(self, *, expected: int, actual: int | None) -> None:
        actual_text = str(actual) if actual is not None else "changed concurrently"
        super().__init__(
            "generator_config_revision_conflict",
            f"Generator configuration revision conflict: expected {expected}, actual {actual_text}",
        )


class GeneratorConfigCatalog:
    """Initialize once from IR, then mutate only the frozen generator models."""

    def __init__(self, unit_of_work_factory: GeneratorConfigUnitOfWorkFactory) -> None:
        self.unit_of_work_factory = unit_of_work_factory

    def get_operation(self, operation_key: str) -> OperationGeneratorConfig | None:
        with self.unit_of_work_factory() as uow:
            return uow.generator_configs.get(operation_key)

    def inspect_operation(self, operation_key: str) -> OperationGeneratorConfig:
        return self._require_existing(operation_key)

    def list_revisions(
        self,
        operation_key: str,
    ) -> list[GeneratorConfigRevision]:
        with self.unit_of_work_factory() as uow:
            return uow.generator_configs.list_revisions(operation_key)

    def get_revision(
        self,
        operation_key: str,
        revision: int,
    ) -> GeneratorConfigRevision | None:
        with self.unit_of_work_factory() as uow:
            return uow.generator_configs.get_revision(operation_key, revision)

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

    def replace_operation(
        self,
        *,
        operation_key: str,
        expected_revision: int,
        active_media_type: str | None,
        configs: Sequence[InputGeneratorConfig],
    ) -> OperationGeneratorConfig:
        current = self._require_existing(operation_key)
        normalized = [
            InputGeneratorConfig.model_validate(config)
            for config in configs
        ]
        media_type = _normalize_media_type(current, active_media_type)
        _validate_configs(
            current,
            media_type=media_type,
            configs=normalized,
            enforce_schema=False,
        )
        return self._replace(
            current,
            expected_revision=expected_revision,
            active_media_type=media_type,
            configs=normalized,
            clear_recoverable_reasons=True,
        )

    def patch_operation(
        self,
        *,
        operation_key: str,
        expected_revision: int,
        updates: Sequence[InputGeneratorPatch],
    ) -> OperationGeneratorConfig:
        current = self._require_existing(operation_key)
        updated, patched_node_ids = _apply_patches(current, updates)
        _validate_configs(
            current,
            media_type=current.active_media_type,
            configs=updated,
            enforce_schema=False,
        )
        return self._replace(
            current,
            expected_revision=expected_revision,
            active_media_type=current.active_media_type,
            configs=updated,
            clear_recoverable_reasons=False,
            cleared_recoverable_node_ids=patched_node_ids,
        )

    def stage_candidate(
        self,
        *,
        operation_key: str,
        expected_revision: int,
        updates: Sequence[InputGeneratorPatch],
        hypothesis: dict,
    ) -> OperationGeneratorConfig:
        current = self._require_existing(operation_key)
        updated, patched_node_ids = _apply_patches(current, updates)
        _validate_configs(
            current,
            media_type=current.active_media_type,
            configs=updated,
            enforce_schema=False,
        )
        return self._replace(
            current,
            expected_revision=expected_revision,
            active_media_type=current.active_media_type,
            configs=updated,
            clear_recoverable_reasons=False,
            cleared_recoverable_node_ids=patched_node_ids,
            lifecycle="candidate",
            hypothesis=dict(hypothesis),
        )

    def accept_candidate(
        self,
        *,
        operation_key: str,
        candidate_revision: int,
        evaluation: dict,
    ) -> GeneratorConfigRevision:
        with self.unit_of_work_factory() as uow:
            current = uow.generator_configs.get(operation_key)
            if current is None or current.revision != candidate_revision:
                raise GeneratorConfigRevisionConflict(
                    expected=candidate_revision,
                    actual=current.revision if current is not None else 0,
                )
            try:
                revision = uow.generator_configs.update_revision(
                    operation_key=operation_key,
                    revision=candidate_revision,
                    expected_lifecycle="candidate",
                    lifecycle="accepted",
                    evaluation=dict(evaluation),
                )
            except GeneratorConfigConcurrentWrite as exc:
                raise GeneratorConfigError(
                    "generator_candidate_state_invalid",
                    "Generator candidate is no longer pending evaluation",
                ) from exc
            uow.commit()
            return revision

    def finalize_candidate(
        self,
        *,
        operation_key: str,
        candidate_revision: int,
        accepted_input_node_ids: set[str],
        evaluation: dict,
    ) -> OperationGeneratorConfig:
        """Atomically accept all, part, or none of a tested candidate."""

        accepted_node_ids = set(accepted_input_node_ids)
        with self.unit_of_work_factory() as uow:
            current = uow.generator_configs.get(operation_key)
            if current is None or current.revision != candidate_revision:
                raise GeneratorConfigRevisionConflict(
                    expected=candidate_revision,
                    actual=current.revision if current is not None else 0,
                )
            candidate = uow.generator_configs.get_revision(
                operation_key,
                candidate_revision,
            )
            if (
                candidate is None
                or candidate.lifecycle != "candidate"
                or candidate.parent_revision is None
            ):
                raise GeneratorConfigError(
                    "generator_candidate_state_invalid",
                    "Generator candidate is not pending or has no parent revision",
                )
            parent = uow.generator_configs.get_revision(
                operation_key,
                candidate.parent_revision,
            )
            if parent is None:
                raise GeneratorConfigError(
                    "generator_candidate_parent_missing",
                    "Generator candidate parent revision does not exist",
                )
            changed_node_ids = _changed_input_node_ids(
                parent.config,
                candidate.config,
            )
            unexpected = sorted(accepted_node_ids - changed_node_ids)
            if unexpected:
                raise GeneratorConfigError(
                    "generator_candidate_invalid_accepted_nodes",
                    "Accepted nodes were not changed by the candidate: "
                    f"{unexpected}",
                    input_node_ids=unexpected,
                )
            try:
                if accepted_node_ids == changed_node_ids:
                    uow.generator_configs.update_revision(
                        operation_key=operation_key,
                        revision=candidate_revision,
                        expected_lifecycle="candidate",
                        lifecycle="accepted",
                        evaluation=dict(evaluation),
                    )
                    uow.commit()
                    return candidate.config

                uow.generator_configs.update_revision(
                    operation_key=operation_key,
                    revision=candidate_revision,
                    expected_lifecycle="candidate",
                    lifecycle="rejected",
                    evaluation=dict(evaluation),
                )
                merged = _merge_candidate_subset(
                    parent.config,
                    candidate.config,
                    accepted_node_ids=accepted_node_ids,
                )
                accepted = uow.generator_configs.replace(
                    operation_key=operation_key,
                    expected_revision=candidate_revision,
                    revision=candidate_revision + 1,
                    snapshot=merged.snapshot.model_dump(mode="json"),
                    enabled=merged.enabled,
                    disabled_reasons=[
                        item.model_dump(mode="json")
                        for item in merged.disabled_reasons
                    ],
                    active_media_type=merged.active_media_type,
                    configs=merged.configs,
                    lifecycle="accepted",
                )
            except GeneratorConfigConcurrentWrite as exc:
                raise GeneratorConfigRevisionConflict(
                    expected=candidate_revision,
                    actual=None,
                ) from exc
            uow.commit()
            return accepted

    def reject_candidate_and_rollback(
        self,
        *,
        operation_key: str,
        candidate_revision: int,
        evaluation: dict,
    ) -> OperationGeneratorConfig:
        with self.unit_of_work_factory() as uow:
            current = uow.generator_configs.get(operation_key)
            if current is None or current.revision != candidate_revision:
                raise GeneratorConfigRevisionConflict(
                    expected=candidate_revision,
                    actual=current.revision if current is not None else 0,
                )
            candidate = uow.generator_configs.get_revision(
                operation_key,
                candidate_revision,
            )
            if (
                candidate is None
                or candidate.lifecycle != "candidate"
                or candidate.parent_revision is None
            ):
                raise GeneratorConfigError(
                    "generator_candidate_state_invalid",
                    "Generator candidate is not pending or has no parent revision",
                )
            restored_from = uow.generator_configs.get_revision(
                operation_key,
                candidate.parent_revision,
            )
            if restored_from is None:
                raise GeneratorConfigError(
                    "generator_candidate_parent_missing",
                    "Generator candidate parent revision does not exist",
                )
            try:
                uow.generator_configs.update_revision(
                    operation_key=operation_key,
                    revision=candidate_revision,
                    expected_lifecycle="candidate",
                    lifecycle="rejected",
                    evaluation=dict(evaluation),
                )
                source = restored_from.config
                restored = uow.generator_configs.replace(
                    operation_key=operation_key,
                    expected_revision=candidate_revision,
                    revision=candidate_revision + 1,
                    snapshot=source.snapshot.model_dump(mode="json"),
                    enabled=source.enabled,
                    disabled_reasons=[
                        item.model_dump(mode="json")
                        for item in source.disabled_reasons
                    ],
                    active_media_type=source.active_media_type,
                    configs=source.configs,
                    lifecycle="rollback",
                    rollback_of_revision=candidate_revision,
                    restored_from_revision=restored_from.revision,
                )
            except GeneratorConfigConcurrentWrite as exc:
                raise GeneratorConfigRevisionConflict(
                    expected=candidate_revision,
                    actual=None,
                ) from exc
            uow.commit()
            return restored

    def recover_interrupted_candidate(
        self,
        operation_key: str,
    ) -> OperationGeneratorConfig:
        current = self._require_existing(operation_key)
        with self.unit_of_work_factory() as uow:
            revision = uow.generator_configs.get_revision(
                operation_key,
                current.revision,
            )
        if revision is None or revision.lifecycle != "candidate":
            return current
        return self.reject_candidate_and_rollback(
            operation_key=operation_key,
            candidate_revision=current.revision,
            evaluation={"stop_reason": "interrupted"},
        )

    def require_operation(self, operation_key: str) -> OperationGeneratorConfig:
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

    def _replace(
        self,
        current: OperationGeneratorConfig,
        *,
        expected_revision: int,
        active_media_type: str | None,
        configs: list[InputGeneratorConfig],
        clear_recoverable_reasons: bool,
        cleared_recoverable_node_ids: set[str] | None = None,
        lifecycle: str = "accepted",
        hypothesis: dict | None = None,
    ) -> OperationGeneratorConfig:
        if current.revision != expected_revision:
            raise GeneratorConfigRevisionConflict(
                expected=expected_revision,
                actual=current.revision,
            )
        remaining_reasons = _contract_disabled_reasons(
            current,
            active_media_type,
        )
        if not clear_recoverable_reasons:
            remaining_reasons.extend(
                reason
                for reason in current.disabled_reasons
                if reason.recoverable
                and reason.input_node_id not in (
                    cleared_recoverable_node_ids or set()
                )
            )
        remaining_reasons = list(
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
        with self.unit_of_work_factory() as uow:
            latest = uow.generator_configs.get(current.operation_key)
            actual_revision = latest.revision if latest is not None else 0
            if actual_revision != expected_revision:
                raise GeneratorConfigRevisionConflict(
                    expected=expected_revision,
                    actual=actual_revision,
                )
            try:
                record = uow.generator_configs.replace(
                    operation_key=current.operation_key,
                    expected_revision=expected_revision,
                    revision=expected_revision + 1,
                    snapshot=current.snapshot.model_dump(mode="json"),
                    enabled=not remaining_reasons,
                    disabled_reasons=[
                        item.model_dump(mode="json") for item in remaining_reasons
                    ],
                    active_media_type=active_media_type,
                    configs=configs,
                    lifecycle=lifecycle,
                    hypothesis=hypothesis,
                )
            except GeneratorConfigConcurrentWrite as exc:
                raise GeneratorConfigRevisionConflict(
                    expected=expected_revision,
                    actual=None,
                ) from exc
            uow.commit()
            return record


def _apply_patches(
    current: OperationGeneratorConfig,
    updates: Sequence[InputGeneratorPatch],
) -> tuple[list[InputGeneratorConfig], set[str]]:
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


def _changed_input_node_ids(
    parent: OperationGeneratorConfig,
    candidate: OperationGeneratorConfig,
) -> set[str]:
    parent_by_id = {item.input_node_id: item for item in parent.configs}
    candidate_by_id = {item.input_node_id: item for item in candidate.configs}
    return {
        input_node_id
        for input_node_id in set(parent_by_id) | set(candidate_by_id)
        if parent_by_id.get(input_node_id) != candidate_by_id.get(input_node_id)
    }


def _merge_candidate_subset(
    parent: OperationGeneratorConfig,
    candidate: OperationGeneratorConfig,
    *,
    accepted_node_ids: set[str],
) -> OperationGeneratorConfig:
    candidate_by_id = {
        item.input_node_id: item for item in candidate.configs
    }
    configs = [
        (
            candidate_by_id[item.input_node_id]
            if item.input_node_id in accepted_node_ids
            else item
        )
        for item in parent.configs
    ]
    remaining_reasons = [
        reason
        for reason in parent.disabled_reasons
        if not (
            reason.recoverable
            and reason.input_node_id in accepted_node_ids
        )
    ]
    return parent.model_copy(
        update={
            "revision": candidate.revision + 1,
            "enabled": not remaining_reasons,
            "disabled_reasons": remaining_reasons,
            "configs": configs,
        }
    )


def _normalize_media_type(
    current: OperationGeneratorConfig,
    active_media_type: str | None,
) -> str | None:
    snapshot = current.snapshot
    if snapshot.request_body_node_id is None:
        if active_media_type is not None:
            raise GeneratorConfigError(
                "generator_config_invalid_media_type",
                "Operation has no request body",
            )
        return None
    if active_media_type is None:
        raise GeneratorConfigError(
            "generator_config_invalid_media_type",
            "Operation request body requires an active media type",
        )
    normalized = active_media_type.strip().lower()
    if normalized not in snapshot.available_media_types:
        raise GeneratorConfigError(
            "generator_config_invalid_media_type",
            f"Unknown or unsupported active request media type: {active_media_type}",
        )
    return normalized


def _validate_initial_record(
    record: OperationGeneratorConfig,
) -> OperationGeneratorConfig:
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
            if isinstance(strategy, RandomStringGenerator | FormatGenerator)
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
        if schema.pattern or schema.format or schema.has_const or schema.enum:
            return False
        return all(
            schema_matches(schema, value)
            for value in (
                strategy.alphabet[:1] * strategy.min_length,
                strategy.alphabet[:1] * strategy.max_length,
            )
        )
    if isinstance(strategy, FormatGenerator):
        if schema.pattern or schema.has_const or schema.enum:
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
