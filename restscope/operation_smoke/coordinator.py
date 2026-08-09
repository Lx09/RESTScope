"""Coordinate complete Batches around one continuous Resolution Agent.

Each failed Batch enters exactly one Failure Resolution session. The Agent owns
semantic grouping, investigation, worklist evolution, Patch selection, and its
finish decision. This Coordinator owns only Batch sequencing, one Operation-
wide 1000-model-output guard, runtime reference preparation, and the decision
to run another complete Batch after at least one Patch was atomically applied.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from restscope.tools.openapi import operation_input_references
from restscope.tools.context import ToolContext
from restscope.llm import ProviderUnavailableError
from restscope.observability import TracingRuntime
from restscope.operation_smoke.failure_resolution import (
    FailureResolutionAgent,
    FailureResolutionRequest,
)
from restscope.operation_smoke.output_limit import ModelOutputLimit
from restscope.operation_smoke.parameter_patch import (
    CompiledConstraintPatch,
    GeneratorPatchDraft,
    sample_compiled_patch,
)
from restscope.harness.operation_testing.test_case_catalog import TestCaseCatalog
from restscope.harness.operation_testing import BatchExecutionResult
from restscope.request_generation import (
    ConstraintSet,
    RequestGenerationConfigStore,
    OperationGeneratorConfig,
    ResourceIdentifierGenerator,
    ResponseValueGenerator,
    build_semantic_input_map,
    referenced_input_node_ids,
)
from restscope.request_generation.constraints import OperationConstraintRecord

from .references import BehaviorMonitorReferenceValues
from .schemas import (
    OperationSmokeFailureKind,
    OperationSmokeRequest,
    OperationSmokeResult,
    ResolutionItemSummary,
    ResolutionPatchSummary,
    SmokeRoundSummary,
)


class SmokeBatchRunner(Protocol):
    """Run every case in one deterministic complete Smoke Batch."""

    def run_smoke_batch(
        self,
        context: ToolContext,
        /,
        *,
        operation_key: str,
        case_count: int,
        seed: int,
        constraints: ConstraintSet | None,
        case_id_factory: Callable[[], str] | None = None,
    ) -> BatchExecutionResult:
        """Execute one full Batch and return Catalog-ready Test Cases."""
        ...


class CurrentConstraintReader(Protocol):
    """Read the operation's executable Constraints from durable current state."""

    def current_constraints(
        self,
        operation_key: str,
    ) -> list[OperationConstraintRecord]:
        """Return the complete current Constraint set for one operation."""
        ...


class OperationSmokeCoordinator:
    """Repeat complete Batches until success or Resolution applies no Patch."""

    def __init__(
        self,
        *,
        config_store: RequestGenerationConfigStore,
        batch_runner: SmokeBatchRunner,
        failure_resolution_agent: FailureResolutionAgent,
        constraint_reader: CurrentConstraintReader,
        reference_values: BehaviorMonitorReferenceValues,
        random_seed: int = 0,
        tracing_runtime: TracingRuntime | None = None,
    ) -> None:
        """Store workflow collaborators; sessions retain all mutable Agent state."""
        self.config_store = config_store
        self.batch_runner = batch_runner
        self.failure_resolution_agent = failure_resolution_agent
        self.constraint_reader = constraint_reader
        self.reference_values = reference_values
        self.random_seed = random_seed
        self.tracing_runtime = tracing_runtime or TracingRuntime.disabled()

    def run(
        self,
        context: ToolContext,
        request: OperationSmokeRequest,
    ) -> OperationSmokeResult:
        """Run the traced workflow and return only bounded public summaries."""
        with self.tracing_runtime.span(
            "OperationSmokeCoordinator.run",
            kind="CHAIN",
            input_value={
                "operation_key": request.operation_key,
                "case_count": request.case_count,
                "success_rate_threshold": request.success_rate_threshold,
                "random_seed": self.random_seed,
            },
            attributes={
                "restscope.operation.key": request.operation_key,
                "restscope.smoke.case_count": request.case_count,
                "restscope.random.seed": self.random_seed,
            },
        ) as span:
            result = self._run(context, request)
            span.set_output(
                {
                    "status": result.status,
                    "stop_reason": result.stop_reason,
                    "success_rate": result.success_rate,
                    "round_count": len(result.rounds),
                    "batch_run_ids": result.batch_run_ids,
                }
            )
            if result.status == "errored":
                span.mark_error("Operation Smoke returned an errored result")
            return result

    def clear_app_state(self) -> None:
        """Retain a no-op close hook; current state lives in the database."""

    def _run(
        self,
        context: ToolContext,
        request: OperationSmokeRequest,
    ) -> OperationSmokeResult:
        """Execute complete rounds and catch technical errors at the workflow edge."""
        current = self.config_store.get_operation(request.operation_key)
        if current is None:
            return _errored_result(
                request=request,
                success_rate=0,
                batch_run_ids=[],
                rounds=[],
                failure_kind="operation_error",
                error=ValueError(
                    f"No Generator configuration exists for {request.operation_key}"
                ),
            )
        if not current.enabled:
            return OperationSmokeResult(
                status="unsupported",
                operation_key=request.operation_key,
                success_rate=0,
                required_success_rate=request.success_rate_threshold,
                failure_kind="unsupported_operation",
                reason="The operation has no executable Generator configuration.",
            )

        batch_run_ids: list[str] = []
        rounds: list[SmokeRoundSummary] = []
        success_rate = 0.0
        output_limit = ModelOutputLimit()
        operation = context.ir.operations.get(request.operation_key)
        semantic = build_semantic_input_map(current)
        catalog = TestCaseCatalog(
            input_references=(
                operation_input_references(operation)
                if operation is not None
                else semantic.reference_by_handle.values()
            )
        )

        try:
            while True:
                constraints = _compiled_constraints(
                    self.constraint_reader.current_constraints(
                        request.operation_key
                    )
                )
                _assert_reference_invariants(current, self.reference_values)
                batch = self.batch_runner.run_smoke_batch(
                    context,
                    operation_key=request.operation_key,
                    case_count=request.case_count,
                    seed=self.random_seed,
                    constraints=_combined_constraints(constraints),
                    case_id_factory=catalog.issue_case_id,
                )
                batch_run_ids.append(batch.run_id)
                for case in batch.cases:
                    catalog.record(case)
                success_rate = batch.success_rate
                if success_rate >= request.success_rate_threshold:
                    return _passed_result(
                        request=request,
                        success_rate=success_rate,
                        batch_run_ids=batch_run_ids,
                        rounds=rounds,
                        stop_reason="success_rate_reached",
                        reason=(
                            "The latest complete Batch reached the required "
                            "success rate."
                        ),
                    )

                round_number = len(rounds) + 1
                resolution = self.failure_resolution_agent.start(
                    FailureResolutionRequest(
                        operation_key=request.operation_key,
                        round_number=round_number,
                        batch_run_id=batch.run_id,
                        case_ids=[
                            case.case_id
                            for case in batch.cases
                            if case.failure is not None
                        ],
                    ),
                    catalog=catalog,
                    output_limit=output_limit,
                    config=current,
                    active_constraints=constraints,
                    case_count=request.case_count,
                    random_seed=self.random_seed,
                    prepare_patch_updates=(
                        lambda config, updates, selected: _prepare_reference_updates(
                            self.reference_values,
                            context=context,
                            config=config,
                            updates=updates,
                            selected_reference_provenance=selected,
                        )
                    ),
                    validate_combined_patch=(
                        lambda patch: sample_compiled_patch(
                            config=current,
                            patch=patch,
                            active_constraints=constraints,
                            affected_parameters=_affected_patch_handles(
                                current,
                                patch,
                            ),
                            reference_values=self.reference_values,
                            case_count=request.case_count,
                            random_seed=self.random_seed,
                        )
                    ),
                ).advance()
                if resolution.status == "failure_resolution_limit_exceeded":
                    return _errored_result(
                        request=request,
                        success_rate=success_rate,
                        batch_run_ids=batch_run_ids,
                        rounds=rounds,
                        failure_kind="failure_resolution_limit_exceeded",
                        error=RuntimeError(
                            resolution.reason
                            or "Failure Resolution reached its hard output limit."
                        ),
                    )

                assert resolution.commit is not None
                work_item_by_id = {
                    item.item_id: item for item in resolution.worklist.items
                }
                item_summaries = []
                for committed in resolution.commit.items:
                    work_item = work_item_by_id[committed.item_id]
                    assert work_item.decision is not None
                    patch_summary = None
                    if committed.outcome == "apply_patch":
                        assert committed.candidate_ref is not None
                        assert committed.patch_outputs is not None
                        assert committed.generator_change_event_id is not None
                        assert committed.changed_input_count is not None
                        assert committed.constraint_count is not None
                        patch_summary = ResolutionPatchSummary(
                            candidate_ref=committed.candidate_ref,
                            patch_outputs=committed.patch_outputs,
                            generator_change_event_id=(
                                committed.generator_change_event_id
                            ),
                            changed_input_count=committed.changed_input_count,
                            constraint_count=committed.constraint_count,
                        )
                    item_summaries.append(
                        ResolutionItemSummary(
                            item_id=committed.item_id,
                            # Finalization derives the display summary from
                            # trusted E messages; the worklist no longer lets
                            # the Agent author a duplicate of root_cause.
                            failure_summary=committed.failure_summary,
                            outcome=committed.outcome,
                            attempt_id=committed.attempt_id,
                            reason=work_item.decision.reason,
                            applied_patch=patch_summary,
                        )
                    )
                rounds.append(
                    SmokeRoundSummary(
                        round_number=round_number,
                        batch_run_id=batch.run_id,
                        resolution_status="completed",
                        resolution_outputs=resolution.outputs_used,
                        failure_count=resolution.source_count,
                        items=item_summaries,
                    )
                )
                if not resolution.commit.applied_candidate_refs:
                    return _passed_result(
                        request=request,
                        success_rate=success_rate,
                        batch_run_ids=batch_run_ids,
                        rounds=rounds,
                        stop_reason="no_patch_applied",
                        reason=(
                            "Failure Resolution finished without applying a "
                            "Patch, so no further Batch is required."
                        ),
                    )

                # Finalization committed all selected candidates atomically.
                # Reload durable state before the next complete Batch rather
                # than treating session objects as current configuration.
                current = self.config_store.require_operation(
                    request.operation_key
                )
        except Exception as exc:
            failure_kind: OperationSmokeFailureKind = (
                "provider_unavailable"
                if isinstance(exc, ProviderUnavailableError)
                else "operation_error"
            )
            return _errored_result(
                request=request,
                success_rate=success_rate,
                batch_run_ids=batch_run_ids,
                rounds=rounds,
                failure_kind=failure_kind,
                error=exc,
            )


def _passed_result(
    *,
    request: OperationSmokeRequest,
    success_rate: float,
    batch_run_ids: list[str],
    rounds: list[SmokeRoundSummary],
    stop_reason,
    reason: str,
) -> OperationSmokeResult:
    """Build one explicit successful terminal result."""
    return OperationSmokeResult(
        status="passed",
        operation_key=request.operation_key,
        success_rate=success_rate,
        required_success_rate=request.success_rate_threshold,
        batch_run_ids=batch_run_ids,
        rounds=rounds,
        stop_reason=stop_reason,
        reason=reason,
    )


def _errored_result(
    *,
    request: OperationSmokeRequest,
    success_rate: float,
    batch_run_ids: list[str],
    rounds: list[SmokeRoundSummary],
    failure_kind: OperationSmokeFailureKind,
    error: Exception,
) -> OperationSmokeResult:
    """Convert a technical failure into a Run-Harness-readable result."""
    return OperationSmokeResult(
        status="errored",
        operation_key=request.operation_key,
        success_rate=success_rate,
        required_success_rate=request.success_rate_threshold,
        batch_run_ids=batch_run_ids,
        rounds=rounds,
        failure_kind=failure_kind,
        error={"type": type(error).__name__, "message": str(error)},
    )


def _assert_reference_invariants(
    config: OperationGeneratorConfig,
    reference_values: BehaviorMonitorReferenceValues,
) -> None:
    """Stop before generation if an active observed-value pool is empty."""
    for item in config.configs:
        strategy = item.strategy
        if not isinstance(
            strategy,
            (ResourceIdentifierGenerator, ResponseValueGenerator),
        ):
            continue
        if reference_values.values_for(strategy):
            continue
        raise RuntimeError(
            "Reference Generator invariant violated: "
            f"{item.input_node_id} uses an empty {strategy.type} pool"
        )


def _prepare_reference_updates(
    reference_values: BehaviorMonitorReferenceValues,
    *,
    context: ToolContext,
    config: OperationGeneratorConfig,
    updates,
    selected_reference_provenance,
):
    """Register selected observed pools and verify they remain non-empty."""
    prepared = reference_values.prepare_updates(
        ir=context.ir,
        config=config,
        updates=updates,
        selected_reference_provenance=selected_reference_provenance,
    )
    for update in prepared:
        strategy = update.strategy
        if isinstance(
            strategy,
            (ResourceIdentifierGenerator, ResponseValueGenerator),
        ) and not reference_values.values_for(strategy):
            raise RuntimeError(
                "Selected reference Generator pool is empty for "
                f"{update.input_node_id}"
            )
    return prepared


def _affected_patch_handles(
    config: OperationGeneratorConfig,
    patch: GeneratorPatchDraft,
) -> list[str]:
    """Translate every combined Generator/Constraint scope into semantic handles."""
    semantic = build_semantic_input_map(config)
    node_ids = {
        update.input_node_id for update in patch.updates
    } | {
        node_id
        for constraint in patch.constraints
        for node_id in referenced_input_node_ids(constraint.constraint)
    }
    unknown = sorted(node_ids - set(semantic.handle_by_node))
    if unknown:
        raise ValueError(
            "Combined Patch references unknown operation inputs: "
            + ", ".join(unknown)
        )
    return sorted(semantic.handle_by_node[node_id] for node_id in node_ids)


def _combined_constraints(
    patches: list[CompiledConstraintPatch],
) -> ConstraintSet | None:
    """Combine accepted Constraints by stable identity for Batch execution."""
    unique = {patch.constraint_id: patch for patch in patches}
    expressions = [
        expression
        for patch in unique.values()
        for expression in patch.constraint.constraints
    ]
    return ConstraintSet(constraints=expressions) if expressions else None


def _compiled_constraints(
    records: list[OperationConstraintRecord],
) -> list[CompiledConstraintPatch]:
    """Translate durable records into the execution DTO used by Smoke."""
    return [
        CompiledConstraintPatch(
            constraint_id=item.id,
            kind=item.kind,
            constraint=item.constraint,
        )
        for item in records
    ]
