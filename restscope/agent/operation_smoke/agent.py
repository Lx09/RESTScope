"""Coordinate complete batches and independent LLM-led Smoke roles.

``OperationSmokeAgent`` deliberately contains no deterministic root-cause,
failure-grouping, ownership, or semantic effect rules. It runs complete
same-seed batches, fixes each Plan round's todo order, protects candidate
transactions, combines accepted runtime Constraints, and computes the final
2xx rate used by Supervisor.
"""

from __future__ import annotations

from dataclasses import asdict
import secrets
from typing import Literal, Protocol

from sqlalchemy.exc import SQLAlchemyError

from restscope.capabilities.tool_context import ToolContext
from restscope.agent.failure_solver import (
    FailureSolveAgentFactory,
    FailureSolveRequest,
)
from restscope.agent.parameter_patch import (
    AvailableReferenceOption,
    CompiledConstraintPatch,
    ParameterPatchAgentFactory,
    ParameterPatchFailure,
    ParameterPatchTask,
)
from restscope.agent.smoke_effect import SmokeEffectAgent, SmokeEffectRequest
from restscope.agent.smoke_plan import SmokePlanAgent, SmokePlanRequest
from restscope.observability import TracingRuntime
from restscope.testing import (
    ConstraintSet,
    GeneratorConfigCatalog,
    OperationExecutionReport,
    OperationGeneratorConfig,
    ResourceIdentifierGenerator,
    ResponseValueGenerator,
    SmokeExecutionOutcome,
    build_semantic_input_map,
    expand_generator_patch_presence,
)

from .evidence import build_batch_evidence, build_plan_case_map
from .history import OperationSmokeHistory
from .references import BehaviorMonitorReferenceValues
from .schemas import (
    OperationSmokeFailureKind,
    OperationSmokeRequest,
    OperationSmokeResult,
    OperationSmokeStatus,
    PatchAttemptSummary,
    SmokeRoundSummary,
    TodoRunSummary,
)


class SmokeBatchRunner(Protocol):
    """Run a complete same-seed Batch and return Smoke-only response evidence."""

    def run_smoke_batch(
        self,
        context: ToolContext,
        /,
        *,
        operation_key: str,
        case_count: int,
        seed: int,
        constraints: ConstraintSet | None,
    ) -> SmokeExecutionOutcome: ...


class OperationSmokeAgent:
    """Run Plan → Solve → Patch → Effect until success or Plan stops."""

    def __init__(
        self,
        *,
        config_catalog: GeneratorConfigCatalog,
        batch_runner: SmokeBatchRunner,
        plan_agent: SmokePlanAgent,
        failure_solver_factory: FailureSolveAgentFactory,
        patch_agent_factory: ParameterPatchAgentFactory,
        effect_agent: SmokeEffectAgent,
        reference_values: BehaviorMonitorReferenceValues,
        history: OperationSmokeHistory | None = None,
        tracing_runtime: TracingRuntime | None = None,
    ) -> None:
        """Store collaborators and the App-lifetime, operation-isolated ledger."""
        self.config_catalog = config_catalog
        self.batch_runner = batch_runner
        self.plan_agent = plan_agent
        self.failure_solver_factory = failure_solver_factory
        self.patch_agent_factory = patch_agent_factory
        self.effect_agent = effect_agent
        self.reference_values = reference_values
        self.history = history or OperationSmokeHistory()
        self.tracing_runtime = tracing_runtime or TracingRuntime.disabled()

    def run(
        self,
        context: ToolContext,
        request: OperationSmokeRequest,
    ) -> OperationSmokeResult:
        """Run one traced lifecycle without exposing raw ledger evidence."""
        with self.tracing_runtime.span(
            "OperationSmokeAgent.run",
            kind="AGENT",
            input_value={
                "operation_key": request.operation_key,
                "case_count": request.case_count,
                "success_rate_threshold": request.success_rate_threshold,
                "max_plan_outputs": request.max_plan_outputs,
                "max_solve_outputs_per_todo": (
                    request.max_solve_outputs_per_todo
                ),
                "max_patch_outputs": request.max_patch_outputs,
                "max_effect_outputs": request.max_effect_outputs,
                "continuation_interval": request.continuation_interval,
                "seed": request.seed,
            },
            attributes={
                "restscope.operation.key": request.operation_key,
                "restscope.smoke.case_count": request.case_count,
            },
        ) as span:
            result = self._run(context, request)
            span.set_output(
                {
                    "status": result.status,
                    "success_rate": result.success_rate,
                    "round_count": len(result.rounds),
                    "batch_run_ids": [
                        report.run_id for report in result.batch_reports
                    ],
                    "failure_kind": result.failure_kind,
                }
            )
            if result.status == "errored":
                span.mark_error("Operation Smoke returned an errored result")
            return result

    def clear_app_state(self) -> None:
        """Release raw evidence and runtime Constraints when the App closes."""
        self.history.clear()

    def _run(
        self,
        context: ToolContext,
        request: OperationSmokeRequest,
    ) -> OperationSmokeResult:
        """Execute the thin coordination loop and protect candidate cleanup."""
        try:
            current = self.config_catalog.get_operation(request.operation_key)
            if current is not None:
                current = self.config_catalog.recover_interrupted_candidate(
                    request.operation_key
                )
        except SQLAlchemyError:
            # Catalog availability affects every operation, so Supervisor must
            # stop the run instead of treating it as one endpoint's failure.
            raise
        except Exception as exc:
            return OperationSmokeResult(
                status="errored",
                operation_key=request.operation_key,
                success_rate=0,
                required_success_rate=request.success_rate_threshold,
                active_config_revision=1,
                failure_kind="operation_error",
                error={"type": type(exc).__name__, "message": str(exc)},
            )
        if current is None:
            return OperationSmokeResult(
                status="errored",
                operation_key=request.operation_key,
                success_rate=0,
                required_success_rate=request.success_rate_threshold,
                active_config_revision=1,
                failure_kind="operation_error",
                error={
                    "type": "GeneratorConfigError",
                    "message": (
                        "No generator configuration exists for "
                        f"{request.operation_key}"
                    ),
                },
            )
        if not current.enabled:
            return self._result(
                status="unsupported",
                request=request,
                current=current,
                success_rate=0,
                reports=[],
                rounds=[],
                failure_kind="unsupported_operation",
            )

        operation_state = self.history.state_for(request.operation_key)
        active_constraints = operation_state.accepted_constraints
        reports: list[OperationExecutionReport] = []
        rounds: list[SmokeRoundSummary] = []
        success_rate = 0.0
        seed = request.seed if request.seed is not None else secrets.randbits(63)
        plan_outputs_used = 0
        pending_candidate = False
        pending_change_count = 0

        try:
            while True:
                _assert_reference_invariants(current, self.reference_values)
                report, private = _run_smoke_batch(
                    self.batch_runner,
                    context=context,
                    operation_key=request.operation_key,
                    case_count=request.case_count,
                    seed=seed,
                    constraints=_combined_constraints(
                        list(active_constraints.values())
                    ),
                )
                reports.append(report)
                if report.config_revision != current.revision:
                    raise RuntimeError(
                        "Batch report revision does not match the tested config"
                    )
                latest_batch = build_batch_evidence(report, private)
                success_rate = _success_rate(report)
                if success_rate >= request.success_rate_threshold:
                    return self._result(
                        status="passed",
                        request=request,
                        current=current,
                        success_rate=success_rate,
                        reports=reports,
                        rounds=rounds,
                    )
                # A valid final Plan output may still complete its fixed todo
                # snapshot. Once that round ends, no 51st Plan call is allowed.
                if plan_outputs_used >= request.max_plan_outputs:
                    return self._result(
                        status="retry",
                        request=request,
                        current=current,
                        success_rate=success_rate,
                        reports=reports,
                        rounds=rounds,
                        failure_kind="plan_budget_exhausted",
                    )

                round_number = len(rounds) + 1
                self.history.record(
                    request.operation_key,
                    {
                        "kind": "round_batch",
                        "round_number": round_number,
                        "batch": latest_batch,
                    },
                )
                coded_cases, failed_codes = build_plan_case_map(latest_batch)
                remaining = request.max_plan_outputs - plan_outputs_used
                plan = self.plan_agent.plan(
                    SmokePlanRequest(
                        operation_key=request.operation_key,
                        batch=latest_batch,
                        coded_cases=coded_cases,
                        failed_case_codes=failed_codes,
                        history=self.history.snapshot(request.operation_key),
                    ),
                    max_outputs=remaining,
                )
                plan_outputs_used += plan.outputs_used
                self.history.record(
                    request.operation_key,
                    {
                        "kind": "plan_output",
                        "round_number": round_number,
                        "plan": plan.model_dump(mode="json"),
                    },
                )
                todo_summaries: list[TodoRunSummary] = []
                if plan.status != "planned":
                    rounds.append(
                        SmokeRoundSummary(
                            round_number=round_number,
                            baseline_run_id=report.run_id,
                            plan_status=plan.status,
                            plan_outputs=plan.outputs_used,
                            todos=[],
                        )
                    )
                    return self._result(
                        status="retry",
                        request=request,
                        current=current,
                        success_rate=success_rate,
                        reports=reports,
                        rounds=rounds,
                        failure_kind=plan.status,
                    )

                # ``plan.todos`` is a fixed snapshot. New failures from accepted
                # candidates are intentionally deferred to the next outer round.
                for todo in plan.todos:
                    reference_options = _available_reference_options(
                        self.reference_values,
                        context=context,
                        config=current,
                        input_node_ids={
                            item.input_node_id for item in current.configs
                        },
                    )
                    solve_request = FailureSolveRequest(
                        operation_key=request.operation_key,
                        todo=todo,
                        operation=_operation_context(
                            context,
                            config=current,
                        ),
                        generator_config=current.model_dump(mode="json"),
                        current_batch=latest_batch,
                        reference_options=[
                            option.model_dump(mode="json")
                            for option in reference_options
                        ],
                        history=self.history.snapshot(request.operation_key),
                    )
                    session = self.failure_solver_factory.create().start(
                        solve_request,
                        config=current,
                        max_outputs=request.max_solve_outputs_per_todo,
                        continuation_interval=request.continuation_interval,
                    )
                    patch_attempts: list[PatchAttemptSummary] = []
                    feedback: dict | None = None
                    while True:
                        solve = session.advance(feedback=feedback)
                        feedback = None
                        self.history.record(
                            request.operation_key,
                            {
                                "kind": "solve_output",
                                "round_number": round_number,
                                "todo": todo.model_dump(mode="json"),
                                "outcome": solve.model_dump(mode="json"),
                            },
                        )
                        if solve.status != "patch_ready":
                            todo_summaries.append(
                                TodoRunSummary(
                                    todo_id=todo.todo_id,
                                    failure=todo.failure,
                                    status=solve.status,
                                    solve_outputs=solve.outputs_used,
                                    patch_attempts=patch_attempts,
                                )
                            )
                            break

                        assert solve.patch_requirement is not None
                        requirement = solve.patch_requirement
                        task = ParameterPatchTask(
                            todo_id=todo.todo_id,
                            failure=todo.failure,
                            **requirement.model_dump(mode="json"),
                            prior_attempts=self.history.snapshot(
                                request.operation_key
                            ),
                        )
                        semantic = build_semantic_input_map(current)
                        affected_node_ids = {
                            semantic.node_by_handle[handle]
                            for handle in requirement.affected_inputs
                            if handle in semantic.node_by_handle
                        }
                        patch_options = [
                            option
                            for option in reference_options
                            if option.input_node_id in affected_node_ids
                        ]
                        patch = self.patch_agent_factory.create().run(
                            task=task,
                            config=current,
                            active_constraints=list(
                                active_constraints.values()
                            ),
                            case_count=request.case_count,
                            reference_values=self.reference_values,
                            reference_options=patch_options,
                            max_outputs=request.max_patch_outputs,
                        )
                        self.history.record(
                            request.operation_key,
                            {
                                "kind": "patch_output",
                                "round_number": round_number,
                                "todo": todo.model_dump(mode="json"),
                                "requirement": requirement.model_dump(
                                    mode="json"
                                ),
                                "patch": patch.model_dump(mode="json"),
                            },
                        )
                        if isinstance(patch, ParameterPatchFailure):
                            patch_attempts.append(
                                PatchAttemptSummary(
                                    patch_outputs=patch.outputs_used,
                                    patch_status="failed",
                                )
                            )
                            feedback = {
                                "patch_requirement": requirement.model_dump(
                                    mode="json"
                                ),
                                "patch_failure": patch.model_dump(mode="json"),
                            }
                            continue

                        updates = _prepare_reference_updates(
                            self.reference_values,
                            context=context,
                            config=current,
                            updates=patch.patch.updates,
                            selected_reference_options=(
                                patch.patch.selected_reference_options
                            ),
                        )
                        expanded_updates = (
                            expand_generator_patch_presence(current, updates)
                            if updates
                            else []
                        )
                        candidate_constraints = patch.patch.constraints
                        before_batch = latest_batch
                        if expanded_updates:
                            current = self.config_catalog.stage_candidate(
                                operation_key=request.operation_key,
                                expected_revision=current.revision,
                                updates=expanded_updates,
                                hypothesis={
                                    "kind": "operation_smoke_todo_patch",
                                    "todo_id": todo.todo_id,
                                },
                            )
                            pending_candidate = True
                        pending_change_count = len(expanded_updates) + len(
                            candidate_constraints
                        )
                        candidate_report, candidate_private = _run_smoke_batch(
                            self.batch_runner,
                            context=context,
                            operation_key=request.operation_key,
                            case_count=request.case_count,
                            seed=seed,
                            constraints=_combined_constraints(
                                [
                                    *active_constraints.values(),
                                    *candidate_constraints,
                                ]
                            ),
                        )
                        reports.append(candidate_report)
                        if candidate_report.config_revision != current.revision:
                            raise RuntimeError(
                                "Candidate report revision does not match "
                                "tested config"
                            )
                        candidate_batch = build_batch_evidence(
                            candidate_report,
                            candidate_private,
                        )
                        effect = self.effect_agent.validate(
                            SmokeEffectRequest(
                                operation_key=request.operation_key,
                                todo=todo,
                                patch_requirement=requirement,
                                patch=patch.patch.model_dump(mode="json"),
                                before_batch=before_batch,
                                candidate_batch=candidate_batch,
                                history=self.history.snapshot(
                                    request.operation_key
                                ),
                            ),
                            max_outputs=request.max_effect_outputs,
                        )
                        accepted = (
                            effect.outcome
                            == "resolved_without_regression"
                        )
                        patch_attempts.append(
                            PatchAttemptSummary(
                                patch_outputs=patch.outputs_used,
                                patch_status="validated",
                                effect_outcome=effect.outcome,
                                effect_outputs=effect.outputs_used,
                                accepted=accepted,
                            )
                        )
                        self.history.record(
                            request.operation_key,
                            {
                                "kind": "candidate_effect",
                                "round_number": round_number,
                                "todo": todo.model_dump(mode="json"),
                                "requirement": requirement.model_dump(
                                    mode="json"
                                ),
                                "patch": patch.model_dump(mode="json"),
                                "before_batch": before_batch,
                                "candidate_batch": candidate_batch,
                                "effect": effect.model_dump(mode="json"),
                                "accepted": accepted,
                            },
                        )
                        if accepted:
                            if pending_candidate:
                                current = self.config_catalog.accept_candidate(
                                    operation_key=request.operation_key,
                                    candidate_revision=current.revision,
                                    evaluation=_candidate_evaluation(
                                        candidate_report,
                                        request=request,
                                        status="accepted",
                                        change_count=pending_change_count,
                                    ),
                                )
                                pending_candidate = False
                            for constraint in candidate_constraints:
                                active_constraints[
                                    constraint.constraint_id
                                ] = constraint
                            latest_batch = candidate_batch
                            success_rate = _success_rate(candidate_report)
                            todo_summaries.append(
                                TodoRunSummary(
                                    todo_id=todo.todo_id,
                                    failure=todo.failure,
                                    status="resolved",
                                    solve_outputs=solve.outputs_used,
                                    patch_attempts=patch_attempts,
                                )
                            )
                            pending_change_count = 0
                            break

                        if pending_candidate:
                            current = (
                                self.config_catalog.reject_candidate_and_rollback(
                                    operation_key=request.operation_key,
                                    candidate_revision=current.revision,
                                    evaluation=_candidate_evaluation(
                                        candidate_report,
                                        request=request,
                                        status="rejected",
                                        change_count=pending_change_count,
                                    ),
                                )
                            )
                            pending_candidate = False
                        pending_change_count = 0
                        feedback = {
                            "patch_requirement": requirement.model_dump(
                                mode="json"
                            ),
                            "patch": patch.model_dump(mode="json"),
                            "candidate_batch": candidate_batch,
                            "effect": effect.model_dump(mode="json"),
                        }

                rounds.append(
                    SmokeRoundSummary(
                        round_number=round_number,
                        baseline_run_id=report.run_id,
                        plan_status=plan.status,
                        plan_outputs=plan.outputs_used,
                        todos=todo_summaries,
                    )
                )
        except SQLAlchemyError:
            if pending_candidate:
                try:
                    self.config_catalog.reject_candidate_and_rollback(
                        operation_key=request.operation_key,
                        candidate_revision=current.revision,
                        evaluation={
                            "validation_status": "technical_error",
                            "accepted_change_count": 0,
                            "rejected_change_count": pending_change_count,
                        },
                    )
                except Exception:
                    pass
            raise
        except Exception as exc:
            if pending_candidate:
                current = self.config_catalog.reject_candidate_and_rollback(
                    operation_key=request.operation_key,
                    candidate_revision=current.revision,
                    evaluation={
                        "validation_status": "technical_error",
                        "accepted_change_count": 0,
                        "rejected_change_count": pending_change_count,
                    },
                )
            return self._result(
                status="errored",
                request=request,
                current=current,
                success_rate=success_rate,
                reports=reports,
                rounds=rounds,
                failure_kind="operation_error",
                error={"type": type(exc).__name__, "message": str(exc)},
            )

    @staticmethod
    def _result(
        *,
        status: OperationSmokeStatus,
        request: OperationSmokeRequest,
        current: OperationGeneratorConfig,
        success_rate: float,
        reports: list[OperationExecutionReport],
        rounds: list[SmokeRoundSummary],
        failure_kind: OperationSmokeFailureKind | None = None,
        error: dict[str, str] | None = None,
    ) -> OperationSmokeResult:
        """Build the public summary without copying App-only raw evidence."""
        return OperationSmokeResult(
            status=status,
            operation_key=request.operation_key,
            success_rate=success_rate,
            required_success_rate=request.success_rate_threshold,
            active_config_revision=current.revision,
            batch_reports=reports,
            rounds=rounds,
            failure_kind=failure_kind,
            error=error,
        )


def _success_rate(report: OperationExecutionReport) -> float:
    """Compute the sole pass metric from all complete-batch outcomes."""
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
    """Return the complete current Operation IR plus its frozen test snapshot.

    The initialized ``ToolContext`` supplies the live Operation IR, while the
    Catalog snapshot supplies the frozen generation contract.
    """
    operation = context.ir.operations.get(config.operation_key)
    operation_value = asdict(operation) if operation is not None else None
    return {
        "openapi_operation_ir": operation_value,
        "testing_snapshot": config.snapshot.model_dump(mode="json"),
    }


def _candidate_evaluation(
    report: OperationExecutionReport,
    *,
    request: OperationSmokeRequest,
    status: Literal["accepted", "rejected"],
    change_count: int,
) -> dict:
    """Describe all-or-nothing candidate disposition for the Catalog audit."""
    accepted = change_count if status == "accepted" else 0
    return {
        "run_id": report.run_id,
        "success_rate": _success_rate(report),
        "required_threshold": request.success_rate_threshold,
        "validation_status": status,
        "accepted_change_count": accepted,
        "rejected_change_count": change_count - accepted,
    }


def _assert_reference_invariants(
    config: OperationGeneratorConfig,
    reference_values: BehaviorMonitorReferenceValues,
) -> None:
    """Fail before generation when a configured reference pool is empty."""
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
            "Reference generator invariant violated: "
            f"{item.input_node_id} uses an empty {strategy.type} pool"
        )


def _available_reference_options(
    reference_values: BehaviorMonitorReferenceValues,
    *,
    context: ToolContext,
    config: OperationGeneratorConfig,
    input_node_ids: set[str],
) -> list[AvailableReferenceOption]:
    """Return populated Behavior Monitor references for the affected inputs."""
    return list(
        reference_values.available_options(
            ir=context.ir,
            config=config,
            input_node_ids=input_node_ids,
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
    """Resolve selected reference metadata and verify its pool before staging."""
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
                "Selected reference generator pool is empty for "
                f"{update.input_node_id}"
            )
    return prepared


def _combined_constraints(
    patches: list[CompiledConstraintPatch],
) -> ConstraintSet | None:
    """Combine accepted and candidate Constraints by stable ID."""
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
    """Run one complete Batch through the required Smoke execution interface."""
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
