"""Validate and atomically persist only final reference-worklist decisions.

This deterministic harness dereferences every E, TC, Parameter, and P value
against current session registries. It derives the stable Failure summary from
exact E messages, while Agent-written text supplies root cause and decision
rationale. Executable Patch content, affected input identities, samples, and
change events always come from trusted candidate objects and a freshly
recomputed combined state transition.
"""

from __future__ import annotations

from collections.abc import Callable
from types import TracebackType
from typing import Protocol

from restscope.capabilities import ToolFailure
from restscope.operation_smoke.memory import (
    FailureBatchWrite,
    FailureWrite,
    SmokeMemoryRepository,
    SolveAttemptParameterWrite,
    SolveAttemptWrite,
    prepare_smoke_patch,
)
from restscope.operation_smoke.parameter_patch import (
    CompiledConstraintPatch,
    GeneratorPatchDraft,
)
from restscope.operation_smoke.test_case_catalog import HTTPFailure, TestCaseCatalog
from restscope.testing import (
    OperationGeneratorConfig,
    build_semantic_input_map,
    referenced_input_node_ids,
)
from restscope.testing.ports import (
    GeneratorConfigConcurrentWrite,
    GeneratorConfigRepository,
)

from .candidates import PatchCandidate, PatchCandidateRegistry
from .schemas import (
    FailureResolutionRequest,
    FailureSource,
    FailureWorklist,
    ResolutionCommit,
    ResolutionItemCommit,
    WorklistItem,
)


class ResolutionUnitOfWork(Protocol):
    """Expose Smoke and Generator repositories on one final transaction."""

    smoke_memory: SmokeMemoryRepository
    generator_configs: GeneratorConfigRepository

    def __enter__(self) -> "ResolutionUnitOfWork":
        """Open the transaction and bind both repositories."""
        ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Roll back unfinished work and close the transaction."""
        ...

    def commit(self) -> None:
        """Commit every staged Failure, Attempt, state change, and event."""
        ...


class FailureResolutionFinalizer:
    """Turn decided reference items into one mechanically verified transaction."""

    def __init__(self, unit_of_work_factory: Callable[[], ResolutionUnitOfWork]) -> None:
        """Store the factory without opening a database transaction."""
        self.unit_of_work_factory = unit_of_work_factory

    def finalize(
        self,
        *,
        request: FailureResolutionRequest,
        sources: tuple[FailureSource, ...],
        worklist: FailureWorklist,
        candidates: PatchCandidateRegistry,
        catalog: TestCaseCatalog,
        current: OperationGeneratorConfig | None = None,
        active_constraints: list[CompiledConstraintPatch] | None = None,
        prepare_patch_updates: Callable | None = None,
        validate_combined_patch: Callable[[GeneratorPatchDraft], None] | None = None,
    ) -> ResolutionCommit:
        """Validate refs and effects, then commit all decided items together.

        Args:
            request: Current operation, Batch, and round identity.
            sources: Immutable E-to-TC registry built from the failed Batch.
            worklist: Final Agent-owned reference and semantic text snapshot.
            candidates: Session registry that exclusively owns exact P objects.
            catalog: Run-local source of real Test Cases and HTTP status evidence.
            current: Generator baseline used to compile every session candidate.
            active_constraints: Complete Constraint baseline used by candidates.
            prepare_patch_updates: Optional deterministic reference-value
                preparation applied immediately before combined validation.
            validate_combined_patch: Fresh compilation/sampling callback. It
                raises when the combined Patch is not executable.

        Returns:
            Durable identities from the one committed transaction. An empty
            commit is returned without opening a transaction when no item has a
            decision.

        Raises:
            ToolFailure: A model-correctable reference, overlap, baseline, or
                combined-validation problem. No transaction is committed.
            Exception: An unexpected persistence failure after automatic
                rollback, so the workflow boundary can report a technical error.
        """
        source_by_ref = {source.failure_ref: source for source in sources}
        decisions = [item for item in worklist.items if item.decision is not None]
        if not decisions:
            return ResolutionCommit()

        semantic = build_semantic_input_map(current) if current is not None else None
        resolved = [
            self._resolve_item(
                item=item,
                source_by_ref=source_by_ref,
                candidates=candidates,
                semantic=semantic,
                catalog=catalog,
            )
            for item in decisions
        ]
        selected = [item.candidate for item in resolved if item.candidate is not None]
        selected_refs = [candidate.candidate_ref for candidate in selected]
        if len(selected_refs) != len(set(selected_refs)):
            self._reject(
                code="patch_candidate_selected_more_than_once",
                message="A Patch candidate may be selected by at most one final item.",
            )

        combined_patch = None
        individual_effects = {}
        combined_effect = None
        if selected:
            if current is None or active_constraints is None:
                raise RuntimeError("Patch finalization requires the current baseline")
            self._require_non_overlapping_scopes(selected, active_constraints)
            combined_patch = GeneratorPatchDraft(
                updates=[update for item in selected for update in item.patch.updates],
                constraints=[
                    constraint
                    for item in selected
                    for constraint in item.patch.constraints
                ],
                selected_reference_provenance=[
                    provenance
                    for item in selected
                    for provenance in item.patch.selected_reference_provenance
                ],
            )
            if prepare_patch_updates is not None:
                try:
                    prepared_updates = prepare_patch_updates(
                        current,
                        combined_patch.updates,
                        combined_patch.selected_reference_provenance,
                    )
                except ValueError as exc:
                    self._reject(
                        code="patch_reference_evidence_changed",
                        message=str(exc),
                    )
                combined_patch = combined_patch.model_copy(
                    update={"updates": prepared_updates}
                )
            if validate_combined_patch is None:
                raise RuntimeError("Patch finalization requires fresh combined sampling")
            try:
                validate_combined_patch(combined_patch)
                combined_effect = prepare_smoke_patch(
                    current=current,
                    expected_constraints=active_constraints,
                    patch=combined_patch,
                )
                prepared_by_node = {
                    update.input_node_id: update for update in combined_patch.updates
                }
                for candidate in selected:
                    candidate_patch = candidate.patch.model_copy(
                        update={
                            "updates": [
                                prepared_by_node[update.input_node_id]
                                for update in candidate.patch.updates
                            ]
                        }
                    )
                    individual_effects[candidate.candidate_ref] = prepare_smoke_patch(
                        current=current,
                        expected_constraints=active_constraints,
                        patch=candidate_patch,
                    )
            except ValueError as exc:
                self._reject(
                    code="combined_patch_validation_failed",
                    message=str(exc),
                )

        failure_writes, failure_key_by_item = _failure_writes(resolved)
        try:
            with self.unit_of_work_factory() as uow:
                recorded = uow.smoke_memory.record_failures(
                    FailureBatchWrite(
                        operation_key=request.operation_key,
                        failures=failure_writes,
                    )
                )
                failure_id_by_key = {
                    key: result.failure_id
                    for key, result in zip(
                        [key for key, _write in _unique_failure_writes(resolved)],
                        recorded.failures,
                        strict=True,
                    )
                }
                if combined_effect is not None:
                    assert current is not None
                    uow.generator_configs.replace_inputs(
                        operation_key=request.operation_key,
                        expected=current.configs,
                        updated=combined_effect.config.configs,
                    )
                    uow.generator_configs.replace_constraints(
                        operation_key=request.operation_key,
                        expected=list(combined_effect.expected_constraints),
                        updated=list(combined_effect.constraints),
                    )

                committed_items: list[ResolutionItemCommit] = []
                event_ids: list[str] = []
                for item in resolved:
                    failure_id = failure_id_by_key[failure_key_by_item[item.item.item_id]]
                    attempt_id = uow.smoke_memory.record_solve_attempt(
                        SolveAttemptWrite(
                            operation_key=request.operation_key,
                            failure_id=failure_id,
                            round_number=request.round_number,
                            outcome=(
                                "applied_patch"
                                if item.candidate is not None
                                else "no_patch"
                            ),
                            # The terminal Attempt explains why Resolution
                            # selected this outcome. Exact Patch construction
                            # rationale remains separately authoritative on the
                            # registry candidate and its change event.
                            reason=item.item.decision.reason,
                            root_cause=item.item.root_cause,
                            parameters=item.parameters,
                        )
                    )
                    event_id = None
                    if item.candidate is not None:
                        effect = individual_effects[item.candidate.candidate_ref]
                        event_id = uow.generator_configs.record_change_event(
                            solve_attempt_id=attempt_id,
                            operation_key=request.operation_key,
                            reason=item.candidate.change_reason,
                            generator_changes=list(effect.generator_changes),
                            constraint_changes=list(effect.constraint_changes),
                        )
                        event_ids.append(event_id)
                    committed_items.append(
                        ResolutionItemCommit(
                            item_id=item.item.item_id,
                            failure_summary=item.failure_summary,
                            outcome=item.item.decision.outcome,
                            failure_id=failure_id,
                            attempt_id=attempt_id,
                            candidate_ref=(
                                item.candidate.candidate_ref
                                if item.candidate is not None
                                else None
                            ),
                            generator_change_event_id=event_id,
                            patch_outputs=(
                                item.candidate.outputs_used
                                if item.candidate is not None
                                else None
                            ),
                            changed_input_count=(
                                len(item.candidate.patch.updates)
                                if item.candidate is not None
                                else None
                            ),
                            constraint_count=(
                                len(item.candidate.patch.constraints)
                                if item.candidate is not None
                                else None
                            ),
                        )
                    )
                uow.commit()
        except GeneratorConfigConcurrentWrite:
            self._reject(
                code="patch_baseline_changed",
                message=(
                    "Current Generator or Constraint state changed after the "
                    "selected candidates were validated. Investigate again."
                ),
            )

        return ResolutionCommit(
            items=committed_items,
            attempt_ids=[item.attempt_id for item in committed_items],
            generator_change_event_ids=event_ids,
            applied_candidate_refs=selected_refs,
        )

    def _resolve_item(
        self,
        *,
        item: WorklistItem,
        source_by_ref: dict[str, FailureSource],
        candidates: PatchCandidateRegistry,
        semantic,
        catalog: TestCaseCatalog,
    ) -> "_ResolvedItem":
        """Dereference one decision without trusting copied precise facts."""
        assert item.decision is not None
        if item.root_cause is None:
            self._reject(
                code="missing_root_cause",
                message=f"Decided worklist item {item.item_id} requires root_cause.",
            )
        try:
            messages = [source_by_ref[ref].message for ref in item.source_failure_refs]
        except KeyError as exc:
            self._reject(
                code="unknown_failure_source",
                message=f"Unknown Failure source: {exc.args[0]}",
            )
        candidate = None
        if item.decision.outcome == "apply_patch":
            assert item.decision.selected_candidate_ref is not None
            candidate = candidates.get(item.decision.selected_candidate_ref)
            parameters = [
                SolveAttemptParameterWrite(
                    input_node_id=attribution.input_node_id,
                    cause_summary=item.root_cause,
                )
                for attribution in candidate.parameter_attributions
            ]
            suspected_input_node_ids = [
                attribution.input_node_id
                for attribution in candidate.parameter_attributions
            ]
        else:
            if semantic is None and item.suspected_parameters:
                raise RuntimeError("Parameter finalization requires current config")
            try:
                suspected_input_node_ids = [
                    semantic.node_by_handle[handle]
                    for handle in item.suspected_parameters
                ]
            except KeyError as exc:
                self._reject(
                    code="unknown_parameter",
                    message=f"Unknown Parameter: {exc.args[0]}",
                )
            parameters = [
                SolveAttemptParameterWrite(
                    input_node_id=input_node_id,
                    cause_summary=item.root_cause,
                )
                for input_node_id in suspected_input_node_ids
            ]
        return _ResolvedItem(
            item=item,
            messages=messages,
            failure_summary=derive_failure_summary(messages),
            suspected_input_node_ids=suspected_input_node_ids,
            last_status_code=_last_status_code(item.test_case_refs, catalog),
            parameters=parameters,
            candidate=candidate,
        )

    def _require_non_overlapping_scopes(
        self,
        candidates: list[PatchCandidate],
        active_constraints: list[CompiledConstraintPatch],
    ) -> None:
        """Reject candidates whose Generator or transitive Constraint scopes meet."""
        claimed: dict[str, str] = {}
        for candidate in candidates:
            scope = _candidate_scope(candidate, active_constraints)
            overlaps = sorted(set(claimed) & scope)
            if overlaps:
                other_refs = sorted({claimed[node_id] for node_id in overlaps})
                self._reject(
                    code="overlapping_patch_candidates",
                    message=(
                        f"Patch candidate {candidate.candidate_ref} overlaps "
                        f"{', '.join(other_refs)} on: {', '.join(overlaps)}"
                    ),
                )
            claimed.update({node_id: candidate.candidate_ref for node_id in scope})

    @staticmethod
    def _reject(*, code: str, message: str) -> None:
        """Raise one model-safe finalization rejection before commit."""
        raise ToolFailure(code=code, message=message)


class _ResolvedItem:
    """Keep mechanically dereferenced facts together before one transaction."""

    def __init__(
        self,
        *,
        item: WorklistItem,
        messages: list[str],
        failure_summary: str,
        suspected_input_node_ids: list[str],
        last_status_code: int | None,
        parameters: list[SolveAttemptParameterWrite],
        candidate: PatchCandidate | None,
    ) -> None:
        """Store values derived from authoritative session registries."""
        self.item = item
        self.messages = messages
        self.failure_summary = failure_summary
        self.suspected_input_node_ids = suspected_input_node_ids
        self.last_status_code = last_status_code
        self.parameters = parameters
        self.candidate = candidate


def _unique_failure_writes(
    resolved: list[_ResolvedItem],
) -> list[tuple[tuple, FailureWrite]]:
    """Deduplicate stable Failure identity so one Batch increments it once."""
    unique: dict[tuple, FailureWrite] = {}
    for item in resolved:
        key = _failure_identity(item.messages, item.suspected_input_node_ids)
        unique.setdefault(
            key,
            FailureWrite(
                summary=item.failure_summary,
                messages=sorted({" ".join(message.split()) for message in item.messages}),
                suspected_input_node_ids=sorted(item.suspected_input_node_ids),
                last_status_code=item.last_status_code,
            ),
        )
    return list(unique.items())


def _failure_writes(
    resolved: list[_ResolvedItem],
) -> tuple[list[FailureWrite], dict[str, tuple]]:
    """Return unique writes plus each worklist item's stable identity key."""
    unique = _unique_failure_writes(resolved)
    return (
        [write for _key, write in unique],
        {
            item.item.item_id: _failure_identity(
                item.messages,
                item.suspected_input_node_ids,
            )
            for item in resolved
        },
    )


def _failure_identity(messages: list[str], input_node_ids: list[str]) -> tuple:
    """Mirror stable repository identity without creating a database key."""
    return (
        tuple(sorted({" ".join(message.split()) for message in messages})),
        tuple(sorted(input_node_ids)),
    )


def derive_failure_summary(messages: list[str], *, max_chars: int = 1_200) -> str:
    """Build bounded stable display text from authoritative Failure messages.

    The canonical first exact message is the most direct description of what
    the target returned. Canonical sorting makes the display stable even when
    a later Batch issues E references in a different order. When semantic
    grouping combines more messages, a count points readers to the complete
    ``normalized_messages`` evidence without asking the Agent to write a second
    diagnosis. Very long first messages are clipped only in this display field;
    the exact messages remain persisted.

    Args:
        messages: Exact registry messages in any worklist order.
        max_chars: Maximum size accepted by the public Resolution result.

    Returns:
        One deterministic non-empty summary no longer than ``max_chars``.

    Raises:
        ValueError: If finalization is called without a Failure message or with
            an unusable character allowance.
    """
    if not messages:
        raise ValueError("Failure summary requires at least one exact message")
    if max_chars < 2:
        raise ValueError("Failure summary max_chars must be at least 2")

    normalized_messages = sorted({" ".join(message.split()) for message in messages})
    if any(not message for message in normalized_messages):
        raise ValueError("Failure summary messages must not be blank")
    first = normalized_messages[0]
    suffix = (
        f" (+{len(normalized_messages) - 1} related Failure messages)"
        if len(normalized_messages) > 1
        else ""
    )
    available = max_chars - len(suffix)
    if available < 2:
        raise ValueError("Failure summary allowance is too small for its suffix")
    if len(first) > available:
        first = first[: available - 1].rstrip() + "…"
    return first + suffix


def _last_status_code(case_refs: list[str], catalog: TestCaseCatalog) -> int | None:
    """Derive one bounded status fact from real referenced Test Cases only."""
    statuses = [
        case.failure.status_code
        for case_ref in sorted(case_refs, key=lambda value: int(value.removeprefix("TC")))
        for case in [catalog.get_case(case_ref)]
        if isinstance(case.failure, HTTPFailure)
    ]
    return statuses[-1] if statuses else None


def _candidate_scope(
    candidate: PatchCandidate,
    active_constraints: list[CompiledConstraintPatch],
) -> set[str]:
    """Find every input whose Generator or connected Constraint may change."""
    scope = {update.input_node_id for update in candidate.patch.updates}
    if not candidate.patch.constraints:
        return scope
    frontier = {
        node_id
        for constraint in candidate.patch.constraints
        for node_id in referenced_input_node_ids(constraint.constraint)
    }
    changed = True
    while changed:
        changed = False
        for constraint in active_constraints:
            owners = set(referenced_input_node_ids(constraint.constraint))
            if owners & frontier and not owners <= frontier:
                frontier.update(owners)
                changed = True
    return scope | frontier
