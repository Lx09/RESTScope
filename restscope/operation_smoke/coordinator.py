"""Coordinate complete Batch rounds around Dedup and Solve decisions.

The Coordinator owns ordering and stop conditions, not semantic diagnosis.  A
round always executes in this order:

``complete Batch → Failure Dedup → every Failure Solve item → next complete Batch``.

Parameter Patch validation happens inside each Solve session.  No Effect Agent
or candidate Batch exists: a Patch's real effect is observed only when the next
round starts with a complete Batch under all accepted changes.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict
from typing import Protocol

from restscope.capabilities.tool_context import ToolContext
from restscope.capabilities.openapi_lookup import operation_parameter_handles
from restscope.observability import TracingRuntime
from restscope.operation_smoke.failure_solver import (
    FailureSolveAgentFactory,
    FailureSolveRequest,
)
from restscope.operation_smoke.parameter_patch import (
    AvailableReferenceOption,
    CompiledConstraintPatch,
)
from restscope.operation_smoke.failure_dedup import (
    FailureDeduplicator,
    FailureDedupRequest,
)
from restscope.operation_smoke.test_case_catalog import TestCaseCatalog
from restscope.testing import (
    BatchExecutionResult,
    ConstraintSet,
    GeneratorConfigCatalog,
    OperationGeneratorConfig,
    ResourceIdentifierGenerator,
    ResponseValueGenerator,
    build_semantic_input_map,
)
from restscope.testing.constraints import OperationConstraintRecord

from .references import BehaviorMonitorReferenceValues
from .schemas import (
    OperationSmokeFailureKind,
    OperationSmokeRequest,
    OperationSmokeResult,
    PatchAttemptSummary,
    SmokeRoundSummary,
    TodoRunSummary,
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
    """Repeat full Batches until success or one approved terminal condition."""

    def __init__(
        self,
        *,
        config_catalog: GeneratorConfigCatalog,
        batch_runner: SmokeBatchRunner,
        failure_deduplicator: FailureDeduplicator,
        failure_solver_factory: FailureSolveAgentFactory,
        constraint_reader: CurrentConstraintReader,
        reference_values: BehaviorMonitorReferenceValues,
        random_seed: int = 0,
        tracing_runtime: TracingRuntime | None = None,
    ) -> None:
        """Store workflow collaborators and the durable Constraint reader."""
        self.config_catalog = config_catalog
        self.batch_runner = batch_runner
        self.failure_deduplicator = failure_deduplicator
        self.failure_solver_factory = failure_solver_factory
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
        """Retain a no-op close hook; current Constraints live in the database."""

    def _run(
        self,
        context: ToolContext,
        request: OperationSmokeRequest,
    ) -> OperationSmokeResult:
        """Execute complete rounds, catching technical errors at the workflow edge."""
        current = self.config_catalog.get_operation(request.operation_key)
        if current is None:
            return _errored_result(
                request=request,
                current=None,
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

        constraints = _compiled_constraints(
            self.constraint_reader.current_constraints(request.operation_key)
        )
        batch_run_ids: list[str] = []
        rounds: list[SmokeRoundSummary] = []
        success_rate = 0.0
        dedup_outputs_used = 0
        semantic = build_semantic_input_map(current)
        operation = context.ir.operations.get(request.operation_key)
        catalog = TestCaseCatalog(
            valid_parameters=(
                operation_parameter_handles(operation)
                if operation is not None
                else semantic.node_by_handle
            ),
        )

        try:
            while True:
                # Each complete Batch starts from the durable current set. This
                # avoids relying on private Coordinator memory after a Patch.
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
                    constraints=_combined_constraints(
                        constraints
                    ),
                    case_id_factory=catalog.issue_case_id,
                )
                batch_run_ids.append(batch.run_id)
                for case in batch.cases:
                    catalog.record(case)
                success_rate = batch.success_rate
                if success_rate >= request.success_rate_threshold:
                    return _passed_result(
                        request=request,
                        current=current,
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
                dedup = self.failure_deduplicator.deduplicate(
                    FailureDedupRequest(
                        operation_key=request.operation_key,
                        round_number=round_number,
                        batch_run_id=batch.run_id,
                        case_ids=[
                            case.case_id
                            for case in batch.cases
                            if case.failure is not None
                        ],
                        input_node_ids_by_handle=semantic.node_by_handle,
                    ),
                    catalog=catalog,
                    max_outputs=(
                        request.max_dedup_outputs - dedup_outputs_used
                    ),
                )
                dedup_outputs_used += dedup.outputs_used

                if dedup.status == "dedup_budget_exhausted":
                    rounds.append(
                        SmokeRoundSummary(
                            round_number=round_number,
                            batch_run_id=batch.run_id,
                            dedup_status=dedup.status,
                            dedup_outputs=dedup.outputs_used,
                            failure_count=0,
                        )
                    )
                    return _errored_result(
                        request=request,
                        current=current,
                        success_rate=success_rate,
                        batch_run_ids=batch_run_ids,
                        rounds=rounds,
                        failure_kind="dedup_budget_exhausted",
                        error=RuntimeError(dedup.reason),
                    )

                todo_summaries: list[TodoRunSummary] = []
                applied_count = 0
                # ``dedup.todos`` is a fixed snapshot. Even after one Patch,
                # remaining Failures are investigated against the updated
                # Generator state; no intermediate Batch interrupts the round.
                for todo in dedup.todos:
                    reference_options = _available_reference_options(
                        self.reference_values,
                        context=context,
                        config=current,
                    )
                    solve = (
                        self.failure_solver_factory.create()
                        .start(
                            FailureSolveRequest(
                                operation_key=request.operation_key,
                                round_number=round_number,
                                todo=todo,
                                operation=_operation_context(
                                    context,
                                    config=current,
                                ),
                                generator_config=current.model_dump(
                                    mode="json"
                                ),
                                reference_options=[
                                    option.model_dump(mode="json")
                                    for option in reference_options
                                ],
                            ),
                            catalog=catalog,
                            config=current,
                            active_constraints=constraints,
                            case_count=request.case_count,
                            random_seed=self.random_seed,
                            max_outputs=(
                                request.max_solve_outputs_per_todo
                            ),
                            max_patch_outputs=request.max_patch_outputs,
                            continuation_interval=(
                                request.continuation_interval
                            ),
                            prepare_patch_updates=lambda config, updates, selected: (
                                _prepare_reference_updates(
                                    self.reference_values,
                                    context=context,
                                    config=config,
                                    updates=updates,
                                    selected_reference_options=selected,
                                )
                            ),
                        )
                        .advance()
                    )
                    if solve.status == "solve_budget_exhausted":
                        rounds.append(
                            SmokeRoundSummary(
                                round_number=round_number,
                                batch_run_id=batch.run_id,
                                dedup_status=dedup.status,
                                dedup_outputs=dedup.outputs_used,
                                failure_count=len(dedup.todos),
                                todos=todo_summaries,
                            )
                        )
                        return _errored_result(
                            request=request,
                            current=current,
                            success_rate=success_rate,
                            batch_run_ids=batch_run_ids,
                            rounds=rounds,
                            failure_kind="solve_budget_exhausted",
                            error=RuntimeError(
                                solve.reason
                                or "Failure Solve budget was exhausted."
                            ),
                        )

                    applied_summary = None
                    if solve.status == "applied_patch":
                        assert solve.applied_patch is not None
                        assert solve.generator_change_event_id is not None
                        current = self.config_catalog.require_operation(
                            request.operation_key
                        )
                        constraints = list(solve.active_constraints)
                        applied_count += 1
                        applied_summary = PatchAttemptSummary(
                            candidate_ref=(
                                solve.applied_patch.candidate_ref
                            ),
                            patch_outputs=(
                                solve.applied_patch.patch_outputs
                            ),
                            generator_change_event_id=(
                                solve.generator_change_event_id
                            ),
                            changed_input_count=len(
                                solve.applied_patch.patch.updates
                            ),
                            constraint_count=len(
                                solve.applied_patch.patch.constraints
                            ),
                        )
                    todo_summaries.append(
                        TodoRunSummary(
                            todo_id=todo.todo_id,
                            failure=todo.failure,
                            status=solve.status,
                            solve_outputs=solve.outputs_used,
                            solve_attempt_id=solve.solve_attempt_id,
                            reason=solve.reason,
                            applied_patch=applied_summary,
                        )
                    )

                rounds.append(
                    SmokeRoundSummary(
                        round_number=round_number,
                        batch_run_id=batch.run_id,
                        dedup_status=dedup.status,
                        dedup_outputs=dedup.outputs_used,
                        failure_count=len(dedup.todos),
                        todos=todo_summaries,
                    )
                )
                if applied_count == 0:
                    return _passed_result(
                        request=request,
                        current=current,
                        success_rate=success_rate,
                        batch_run_ids=batch_run_ids,
                        rounds=rounds,
                        stop_reason="no_patch_applied",
                        reason=(
                            "Every deduplicated Solve Attempt completed, but none "
                            "produced an applicable Patch."
                        ),
                    )
                # At least one Patch was committed.  Only now does control
                # return to the top for the next complete Batch.
        except Exception as exc:
            return _errored_result(
                request=request,
                current=current,
                success_rate=success_rate,
                batch_run_ids=batch_run_ids,
                rounds=rounds,
                failure_kind="operation_error",
                error=exc,
            )


def _passed_result(
    *,
    request: OperationSmokeRequest,
    current: OperationGeneratorConfig,
    success_rate: float,
    batch_run_ids: list[str],
    rounds: list[SmokeRoundSummary],
    stop_reason,
    reason: str,
) -> OperationSmokeResult:
    """Build one of the two explicit successful terminal results."""
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
    current: OperationGeneratorConfig | None,
    success_rate: float,
    batch_run_ids: list[str],
    rounds: list[SmokeRoundSummary],
    failure_kind: OperationSmokeFailureKind,
    error: Exception,
) -> OperationSmokeResult:
    """Convert a technical boundary failure into a Supervisor-readable result."""
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


def _operation_context(
    context: ToolContext,
    *,
    config: OperationGeneratorConfig,
) -> dict:
    """Combine the live Operation IR with its frozen testing snapshot."""
    operation = context.ir.operations.get(config.operation_key)
    return {
        "openapi_operation_ir": (
            asdict(operation) if operation is not None else None
        ),
        "testing_snapshot": config.snapshot.model_dump(mode="json"),
    }


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


def _available_reference_options(
    reference_values: BehaviorMonitorReferenceValues,
    *,
    context: ToolContext,
    config: OperationGeneratorConfig,
) -> list[AvailableReferenceOption]:
    """Return populated reference sources for every active configurable input."""
    return list(
        reference_values.available_options(
            ir=context.ir,
            config=config,
            input_node_ids={
                item.input_node_id for item in config.configs
            },
        )
    )


def _prepare_reference_updates(
    reference_values: BehaviorMonitorReferenceValues,
    *,
    context: ToolContext,
    config: OperationGeneratorConfig,
    updates,
    selected_reference_options,
):
    """Register selected observed pools and verify they remain non-empty."""
    prepared = reference_values.prepare_updates(
        ir=context.ir,
        config=config,
        updates=updates,
        selected_reference_options=selected_reference_options,
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
