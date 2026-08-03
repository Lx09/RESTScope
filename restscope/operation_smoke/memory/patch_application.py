"""Atomically apply one complete Patch and record its terminal Solve Attempt.

Parameter Patch Agent creates a side-effect-free candidate.  This deterministic
service derives stable Constraint identity and owner sets, computes the exact
Generator/Constraint diff, and commits that diff with the selected Solve
conclusion.  Agents never name database actions or rows to delete.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from hashlib import sha256
import json
from types import TracebackType
from typing import Protocol

from restscope.operation_smoke.parameter_patch import GeneratorPatchDraft
from restscope.testing import (
    OperationConstraintRecord,
    OperationGeneratorConfig,
    classify_constraint,
    normalize_constraint_set,
    prepare_accepted_generator_patch,
    referenced_input_node_ids,
)
from restscope.testing.ports import GeneratorConfigRepository

from .ports import SmokeMemoryRepository
from .schemas import SolveAttemptParameterWrite, SolveAttemptWrite


class AtomicSmokePatchUnitOfWork(Protocol):
    """Expose Generator and Smoke repositories on one transaction."""

    generator_configs: GeneratorConfigRepository
    smoke_memory: SmokeMemoryRepository

    def __enter__(self) -> "AtomicSmokePatchUnitOfWork": ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...


@dataclass(frozen=True)
class PatchSolveAttempt:
    """Carry reviewed candidate facts into the atomic persistence boundary."""

    operation_key: str
    failure_id: str
    round_number: int
    root_cause: str
    change_reason: str
    parameters: list[SolveAttemptParameterWrite]


@dataclass(frozen=True)
class AppliedSmokePatch:
    """Return current state and durable identities after an atomic commit."""

    config: OperationGeneratorConfig
    solve_attempt_id: str
    generator_change_event_id: str
    constraints: tuple[OperationConstraintRecord, ...]


class SmokePatchApplication:
    """Apply one selected candidate using content comparison, not revisions."""

    def __init__(
        self,
        unit_of_work_factory: Callable[[], AtomicSmokePatchUnitOfWork],
    ) -> None:
        """Bind the transaction factory without opening a database session."""

        self.unit_of_work_factory = unit_of_work_factory

    def current_constraints(
        self,
        operation_key: str,
    ) -> list[OperationConstraintRecord]:
        """Read the operation's current executable Constraints."""

        with self.unit_of_work_factory() as uow:
            return uow.generator_configs.get_constraints(operation_key)

    def apply(
        self,
        *,
        current: OperationGeneratorConfig,
        expected_constraints: list[CompiledConstraintPatch],
        patch: GeneratorPatchDraft,
        attempt: PatchSolveAttempt,
    ) -> AppliedSmokePatch:
        """Commit the exact non-empty Patch diff and its Solve Attempt.

        Stored input and Constraint content is compared with the exact state
        shown during candidate sampling inside the transaction. A mismatch
        raises ``GeneratorConfigConcurrentWrite`` and leaves every table
        unchanged. Candidate samples are intentionally absent because they are
        run-local validation evidence.
        """

        if current.operation_key != attempt.operation_key:
            raise ValueError("Patch current state belongs to another operation")
        accepted = (
            prepare_accepted_generator_patch(current, patch.updates)
            if patch.updates
            else current
        )
        generator_changes = _generator_changes(current, accepted)
        new_constraints = normalize_patch_constraints(
            operation_key=attempt.operation_key,
            patches=patch.constraints,
        )
        expected_constraint_records = normalize_patch_constraints(
            operation_key=attempt.operation_key,
            patches=expected_constraints,
        )
        valid_input_ids = {item.input_node_id for item in current.configs}
        invalid_owner_ids = sorted(
            {
                input_node_id
                for item in new_constraints
                for input_node_id in item.owner_input_node_ids
            }
            - valid_input_ids
        )
        if invalid_owner_ids:
            raise ValueError(
                "Constraint references unknown operation inputs: "
                + ", ".join(invalid_owner_ids)
            )

        with self.unit_of_work_factory() as uow:
            updated_constraints = replace_constraint_scope(
                expected_constraint_records,
                new_constraints,
                has_constraint_patch=bool(patch.constraints),
            )
            constraint_changes = _constraint_changes(
                expected_constraint_records,
                updated_constraints,
            )
            if not generator_changes and not constraint_changes:
                raise ValueError("The accepted Patch does not change current state")

            uow.generator_configs.replace_inputs(
                operation_key=attempt.operation_key,
                expected=current.configs,
                updated=accepted.configs,
            )
            uow.generator_configs.replace_constraints(
                operation_key=attempt.operation_key,
                expected=expected_constraint_records,
                updated=updated_constraints,
            )
            solve_attempt_id = uow.smoke_memory.record_solve_attempt(
                SolveAttemptWrite(
                    operation_key=attempt.operation_key,
                    failure_id=attempt.failure_id,
                    round_number=attempt.round_number,
                    outcome="applied_patch",
                    reason=attempt.change_reason,
                    root_cause=attempt.root_cause,
                    parameters=attempt.parameters,
                )
            )
            event_id = uow.generator_configs.record_change_event(
                solve_attempt_id=solve_attempt_id,
                operation_key=attempt.operation_key,
                reason=attempt.change_reason,
                generator_changes=generator_changes,
                constraint_changes=constraint_changes,
            )
            uow.commit()
            return AppliedSmokePatch(
                config=accepted,
                solve_attempt_id=solve_attempt_id,
                generator_change_event_id=event_id,
                constraints=tuple(updated_constraints),
            )


def normalize_patch_constraints(
    *,
    operation_key: str,
    patches,
) -> list[OperationConstraintRecord]:
    """Derive stable IDs and non-empty owners from complete Patch expressions."""

    records: dict[str, OperationConstraintRecord] = {}
    for patch in patches:
        normalized = normalize_constraint_set(patch.constraint)
        owner = sorted(referenced_input_node_ids(normalized))
        if not owner:
            raise ValueError("A persisted Constraint must reference an operation input")
        encoded = json.dumps(
            normalized.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
        )
        identity = sha256(f"{operation_key}:{encoded}".encode()).hexdigest()[:16]
        record = OperationConstraintRecord(
            id=f"constraint_{identity}",
            operation_key=operation_key,
            owner_input_node_ids=owner,
            kind=classify_constraint(normalized.constraints[0]),
            constraint=normalized,
        )
        records[record.id] = record
    return sorted(records.values(), key=lambda item: item.id)


def replace_constraint_scope(
    current: list[OperationConstraintRecord],
    replacement: list[OperationConstraintRecord],
    *,
    has_constraint_patch: bool,
) -> list[OperationConstraintRecord]:
    """Replace every old owner transitively connected to the new owner set.

    A Generator-only Patch passes ``has_constraint_patch=False`` and returns the
    current list unchanged.  Otherwise the overlap frontier starts with only
    inputs referenced by the new expressions.  Old connected owners expand the
    frontier for matching, but those old-only inputs do not become owners of
    the replacement records.
    """

    if not has_constraint_patch:
        return sorted(current, key=lambda item: item.id)
    frontier = {
        input_node_id
        for item in replacement
        for input_node_id in item.owner_input_node_ids
    }
    if not frontier:
        raise ValueError("A Constraint Patch requires at least one owned input")
    selected_ids: set[str] = set()
    changed = True
    while changed:
        changed = False
        for item in current:
            if item.id in selected_ids:
                continue
            owner = set(item.owner_input_node_ids)
            if owner & frontier:
                selected_ids.add(item.id)
                frontier.update(owner)
                changed = True
    retained = [item for item in current if item.id not in selected_ids]
    by_id = {item.id: item for item in [*retained, *replacement]}
    return sorted(by_id.values(), key=lambda item: item.id)


def _generator_changes(
    before: OperationGeneratorConfig,
    after: OperationGeneratorConfig,
) -> list[dict]:
    """Return ordered per-input updates with exact before and after payloads."""

    before_by_id = {item.input_node_id: item for item in before.configs}
    output: list[dict] = []
    for item in after.configs:
        previous = before_by_id[item.input_node_id]
        if previous == item:
            continue
        output.append(
            {
                "action": "update",
                "input_node_id": item.input_node_id,
                "before": previous.model_dump(mode="json"),
                "after": item.model_dump(mode="json"),
            }
        )
    return output


def _constraint_changes(
    before: list[OperationConstraintRecord],
    after: list[OperationConstraintRecord],
) -> list[dict]:
    """Return deterministic insert/delete events; stable rows are omitted."""

    before_by_id = {item.id: item for item in before}
    after_by_id = {item.id: item for item in after}
    output: list[dict] = []
    for constraint_id in sorted(set(before_by_id) - set(after_by_id)):
        output.append(
            {
                "action": "delete",
                "constraint_id": constraint_id,
                "before": before_by_id[constraint_id].model_dump(mode="json"),
                "after": None,
            }
        )
    for constraint_id in sorted(set(after_by_id) - set(before_by_id)):
        output.append(
            {
                "action": "insert",
                "constraint_id": constraint_id,
                "before": None,
                "after": after_by_id[constraint_id].model_dump(mode="json"),
            }
        )
    return output
