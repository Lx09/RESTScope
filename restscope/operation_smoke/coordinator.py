"""Coordinate complete Batch rounds around three LLM-owned decisions.

The Coordinator owns ordering and stop conditions, not semantic diagnosis.  A
round always executes in this order:

``complete Batch → Planner → every Failure Solve item → next complete Batch``.

Parameter Patch validation happens inside each Solve session.  No Effect Agent
or candidate Batch exists: a Patch's real effect is observed only when the next
round starts with a complete Batch under all accepted changes.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Protocol

from restscope.capabilities.tool_context import ToolContext
from restscope.observability import TracingRuntime
from restscope.operation_smoke.failure_solver import (
    FailureSolveAgentFactory,
    FailureSolveRequest,
)
from restscope.operation_smoke.parameter_patch import (
    AvailableReferenceOption,
    CompiledConstraintPatch,
)
from restscope.operation_smoke.plan import SmokePlanAgent, SmokePlanRequest
from restscope.testing import (
    ConstraintSet,
    GeneratorConfigCatalog,
    OperationExecutionReport,
    OperationGeneratorConfig,
    ResourceIdentifierGenerator,
    ResponseValueGenerator,
    SmokeExecutionOutcome,
)

from .evidence import build_batch_evidence, build_plan_case_map
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
    ) -> SmokeExecutionOutcome:
        """Execute one full Batch and return both its report and private evidence."""
        ...


class OperationSmokeCoordinator:
    """Repeat full Batches until success or one approved terminal condition."""

    def __init__(
        self,
        *,
        config_catalog: GeneratorConfigCatalog,
        batch_runner: SmokeBatchRunner,
        plan_agent: SmokePlanAgent,
        failure_solver_factory: FailureSolveAgentFactory,
        reference_values: BehaviorMonitorReferenceValues,
        random_seed: int = 0,
        tracing_runtime: TracingRuntime | None = None,
    ) -> None:
        """Store workflow collaborators and App-lifetime accepted Constraints.

        Generator revisions live in the database.  Constraints are executable
        Patch content without a general persistence consumer, so they remain
        isolated by operation for this App lifetime and are also captured in
        Applied Patch memory.
        """
        self.config_catalog = config_catalog
        self.batch_runner = batch_runner
        self.plan_agent = plan_agent
        self.failure_solver_factory = failure_solver_factory
        self.reference_values = reference_values
        self.random_seed = random_seed
        self.tracing_runtime = tracing_runtime or TracingRuntime.disabled()
        self._constraints_by_operation: dict[
            str,
            dict[str, CompiledConstraintPatch],
        ] = {}

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
                    "batch_run_ids": [
                        report.run_id for report in result.batch_reports
                    ],
                }
            )
            if result.status == "errored":
                span.mark_error("Operation Smoke returned an errored result")
            return result

    def clear_app_state(self) -> None:
        """Release App-lifetime executable Constraints when the App closes."""
        self._constraints_by_operation.clear()

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
                reports=[],
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
                active_config_revision=current.revision,
                failure_kind="unsupported_operation",
                reason="The operation has no executable Generator configuration.",
            )

        constraints = self._constraints_by_operation.setdefault(
            request.operation_key,
            {},
        )
        reports: list[OperationExecutionReport] = []
        rounds: list[SmokeRoundSummary] = []
        success_rate = 0.0
        planner_outputs_used = 0

        try:
            while True:
                _assert_reference_invariants(current, self.reference_values)
                report, private = _run_smoke_batch(
                    self.batch_runner,
                    context=context,
                    operation_key=request.operation_key,
                    case_count=request.case_count,
                    seed=self.random_seed,
                    constraints=_combined_constraints(
                        list(constraints.values())
                    ),
                )
                reports.append(report)
                if report.config_revision != current.revision:
                    raise RuntimeError(
                        "Batch report revision does not match active Generator config"
                    )
                batch = build_batch_evidence(report, private)
                success_rate = _success_rate(report)
                if success_rate >= request.success_rate_threshold:
                    return _passed_result(
                        request=request,
                        current=current,
                        success_rate=success_rate,
                        reports=reports,
                        rounds=rounds,
                        stop_reason="success_rate_reached",
                        reason=(
                            "The latest complete Batch reached the required "
                            "success rate."
                        ),
                    )

                # The Planner budget is shared by every round in this workflow.
                # We always run the Batch first so the final accepted Patch is
                # measured even if no Planner output remains afterward.
                if planner_outputs_used >= request.max_plan_outputs:
                    return _errored_result(
                        request=request,
                        current=current,
                        success_rate=success_rate,
                        reports=reports,
                        rounds=rounds,
                        failure_kind="plan_budget_exhausted",
                        error=RuntimeError(
                            "The Planner output budget was exhausted."
                        ),
                    )

                round_number = len(rounds) + 1
                coded_cases, failed_codes = build_plan_case_map(batch)
                plan = self.plan_agent.plan(
                    SmokePlanRequest(
                        operation_key=request.operation_key,
                        round_number=round_number,
                        batch_run_id=report.run_id,
                        batch=batch,
                        coded_cases=coded_cases,
                        failed_case_codes=failed_codes,
                    ),
                    max_outputs=(
                        request.max_plan_outputs - planner_outputs_used
                    ),
                )
                planner_outputs_used += plan.outputs_used

                if plan.status == "plan_budget_exhausted":
                    rounds.append(
                        SmokeRoundSummary(
                            round_number=round_number,
                            batch_run_id=report.run_id,
                            plan_status=plan.status,
                            plan_outputs=plan.outputs_used,
                            non_debuggable_count=len(plan.non_debuggable),
                        )
                    )
                    return _errored_result(
                        request=request,
                        current=current,
                        success_rate=success_rate,
                        reports=reports,
                        rounds=rounds,
                        failure_kind="plan_budget_exhausted",
                        error=RuntimeError(plan.reason),
                    )
                if plan.status == "no_debug":
                    rounds.append(
                        SmokeRoundSummary(
                            round_number=round_number,
                            batch_run_id=report.run_id,
                            plan_status=plan.status,
                            plan_outputs=plan.outputs_used,
                            non_debuggable_count=len(plan.non_debuggable),
                        )
                    )
                    return _passed_result(
                        request=request,
                        current=current,
                        success_rate=success_rate,
                        reports=reports,
                        rounds=rounds,
                        stop_reason="planner_no_debug",
                        reason=plan.reason,
                    )

                todo_summaries: list[TodoRunSummary] = []
                applied_count = 0
                # ``plan.todos`` is a fixed snapshot.  Even after one Patch,
                # remaining Failures are investigated against the updated
                # Generator state; no intermediate Batch interrupts the Plan.
                for todo in plan.todos:
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
                                current_batch=batch,
                                reference_options=[
                                    option.model_dump(mode="json")
                                    for option in reference_options
                                ],
                            ),
                            config=current,
                            active_constraints=list(constraints.values()),
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
                                batch_run_id=report.run_id,
                                plan_status=plan.status,
                                plan_outputs=plan.outputs_used,
                                non_debuggable_count=len(
                                    plan.non_debuggable
                                ),
                                todos=todo_summaries,
                            )
                        )
                        return _errored_result(
                            request=request,
                            current=current,
                            success_rate=success_rate,
                            reports=reports,
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
                        assert solve.active_config_revision is not None
                        current = self.config_catalog.require_operation(
                            request.operation_key
                        )
                        for constraint in solve.active_constraints:
                            constraints[constraint.constraint_id] = constraint
                        applied_count += 1
                        applied_summary = PatchAttemptSummary(
                            candidate_ref=(
                                solve.applied_patch.candidate_ref
                            ),
                            patch_outputs=(
                                solve.applied_patch.patch_outputs
                            ),
                            applied_revision=solve.active_config_revision,
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
                            investigation_id=solve.investigation_id,
                            reason=solve.reason,
                            applied_patch=applied_summary,
                        )
                    )

                rounds.append(
                    SmokeRoundSummary(
                        round_number=round_number,
                        batch_run_id=report.run_id,
                        plan_status=plan.status,
                        plan_outputs=plan.outputs_used,
                        non_debuggable_count=len(plan.non_debuggable),
                        todos=todo_summaries,
                    )
                )
                if applied_count == 0:
                    return _passed_result(
                        request=request,
                        current=current,
                        success_rate=success_rate,
                        reports=reports,
                        rounds=rounds,
                        stop_reason="no_patch_applied",
                        reason=(
                            "Every planned Investigation completed, but none "
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
                reports=reports,
                rounds=rounds,
                failure_kind="operation_error",
                error=exc,
            )


def _passed_result(
    *,
    request: OperationSmokeRequest,
    current: OperationGeneratorConfig,
    success_rate: float,
    reports: list[OperationExecutionReport],
    rounds: list[SmokeRoundSummary],
    stop_reason,
    reason: str,
) -> OperationSmokeResult:
    """Build one of the three explicit successful terminal results."""
    return OperationSmokeResult(
        status="passed",
        operation_key=request.operation_key,
        success_rate=success_rate,
        required_success_rate=request.success_rate_threshold,
        active_config_revision=current.revision,
        batch_reports=reports,
        rounds=rounds,
        stop_reason=stop_reason,
        reason=reason,
    )


def _errored_result(
    *,
    request: OperationSmokeRequest,
    current: OperationGeneratorConfig | None,
    success_rate: float,
    reports: list[OperationExecutionReport],
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
        active_config_revision=current.revision if current is not None else 1,
        batch_reports=reports,
        rounds=rounds,
        failure_kind=failure_kind,
        error={"type": type(error).__name__, "message": str(error)},
    )


def _success_rate(report: OperationExecutionReport) -> float:
    """Compute the sole pass metric from every outcome in a complete Batch."""
    successes = sum(
        count
        for status, count in report.status_code_counts.items()
        if status.isdigit() and 200 <= int(status) < 300
    )
    total = sum(report.status_code_counts.values()) + report.error_count
    return successes / total if total else 0.0


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


def _run_smoke_batch(
    runner: SmokeBatchRunner,
    *,
    context: ToolContext,
    operation_key: str,
    case_count: int,
    seed: int,
    constraints: ConstraintSet | None,
) -> tuple[OperationExecutionReport, dict[str, object]]:
    """Call the narrow Batch Interface and index private case evidence."""
    outcome = runner.run_smoke_batch(
        context,
        operation_key=operation_key,
        case_count=case_count,
        seed=seed,
        constraints=constraints,
    )
    return (
        outcome.report,
        {item.case_id: item for item in outcome.case_evidence},
    )
