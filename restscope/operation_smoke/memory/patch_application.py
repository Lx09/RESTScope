"""Atomically accept a Generator Patch and record its Investigation.

Parameter Patch Agent creates a side-effect-free candidate.  Failure Solve may
select that candidate, but only this deterministic service changes durable
state.  It uses one Unit of Work for the Generator revision, Parameter links,
Investigation, and Applied Patch record, so a database error rolls everything
back together.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from types import TracebackType
from typing import Any, Protocol

from restscope.operation_smoke.parameter_patch import GeneratorPatchDraft
from restscope.testing import (
    OperationGeneratorConfig,
    prepare_accepted_generator_patch,
)
from restscope.testing.ports import (
    GeneratorConfigConcurrentWrite,
    GeneratorConfigRepository,
)

from .ports import SmokeMemoryRepository
from .schemas import (
    AppliedPatchWrite,
    InvestigationParameterWrite,
    InvestigationWrite,
)


class AtomicSmokePatchUnitOfWork(Protocol):
    """Expose both repositories participating in one Patch commit."""

    generator_configs: GeneratorConfigRepository
    smoke_memory: SmokeMemoryRepository

    def __enter__(self) -> "AtomicSmokePatchUnitOfWork":
        """Open a transaction exposing both required repositories."""
        ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Close the transaction and roll back when a write raised."""
        ...

    def commit(self) -> None:
        """Publish the Generator revision and Memory rows atomically."""
        ...

    def rollback(self) -> None:
        """Discard both Generator and Memory writes in this transaction."""
        ...


@dataclass(frozen=True)
class PatchInvestigation:
    """Carry the model's validated explanation into deterministic persistence."""

    operation_key: str
    failure_id: str
    round_number: int
    trigger_conditions: str
    root_cause: str
    solution: str
    evidence_source: str
    parameters: list[InvestigationParameterWrite]


@dataclass(frozen=True)
class AppliedSmokePatch:
    """Return the committed Generator state and durable Investigation identity."""

    config: OperationGeneratorConfig
    investigation_id: str


class SmokePatchApplication:
    """Commit one selected session candidate with optimistic revision locking."""

    def __init__(
        self,
        unit_of_work_factory: Callable[[], AtomicSmokePatchUnitOfWork],
    ) -> None:
        """Bind the transaction factory without opening a database session."""
        self.unit_of_work_factory = unit_of_work_factory

    def apply(
        self,
        *,
        expected_revision: int,
        patch: GeneratorPatchDraft,
        samples: list[dict[str, Any]],
        before_generators: dict[str, Any],
        after_generators: dict[str, Any],
        investigation: PatchInvestigation,
    ) -> AppliedSmokePatch:
        """Apply and remember one Patch, or leave both stores unchanged.

        Raises:
            GeneratorConfigConcurrentWrite: Another writer changed the active
                Generator revision after Solve built its candidate.
            ValueError: The Patch is empty, incompatible, or the referenced
                Failure does not belong to the operation.
        """
        with self.unit_of_work_factory() as uow:
            current = uow.generator_configs.get(investigation.operation_key)
            if current is None:
                raise ValueError(
                    "Cannot apply a Patch without an operation Generator config"
                )
            if current.revision != expected_revision:
                raise GeneratorConfigConcurrentWrite(
                    investigation.operation_key
                )

            # Recompute the candidate from database state instead of trusting a
            # stale preview returned to the model.  A Constraint-only Patch has
            # no Generator update to compile, but it still needs an accepted
            # revision.  That revision is the durable point that the Applied
            # Patch record names and that the next Batch reports.
            if patch.updates:
                accepted = prepare_accepted_generator_patch(
                    current,
                    patch.updates,
                )
            else:
                accepted = current.model_copy(
                    update={"revision": current.revision + 1}
                )
            persisted = uow.generator_configs.replace(
                operation_key=investigation.operation_key,
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
            investigation_id = uow.smoke_memory.record_investigation(
                InvestigationWrite(
                    operation_key=investigation.operation_key,
                    failure_id=investigation.failure_id,
                    round_number=investigation.round_number,
                    outcome="applied_patch",
                    trigger_conditions=investigation.trigger_conditions,
                    root_cause=investigation.root_cause,
                    solution=investigation.solution,
                    evidence_source=investigation.evidence_source,
                    parameters=investigation.parameters,
                    applied_patch=AppliedPatchWrite(
                        generator_revision=persisted.revision,
                        patch=patch.model_dump(mode="json"),
                        before_generators=before_generators,
                        after_generators=after_generators,
                        samples=samples,
                    ),
                )
            )
            uow.commit()
            return AppliedSmokePatch(
                config=persisted,
                investigation_id=investigation_id,
            )
