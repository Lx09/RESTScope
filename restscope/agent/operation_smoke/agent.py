"""Bounded batch-feedback loop for one operation's smoke test."""

from __future__ import annotations

import secrets
from typing import Protocol

from sqlalchemy.exc import SQLAlchemyError

from restscope.observability import TracingRuntime
from restscope.testing import (
    GeneratorConfigCatalog,
    OperationExecutionReport,
    OperationGeneratorConfig,
    ReferenceValueProvider,
    ResourceIdentifierGenerator,
    ResponseValueGenerator,
)

from .diagnosis import OperationSmokeDiagnoser
from .evidence import build_semantic_input_map
from .schemas import (
    AvailableReferenceOption,
    OperationSmokeRequest,
    OperationSmokeResult,
    PlanSolveDiagnosisResult,
)


class OperationBatchRunner(Protocol):
    def run_operation(
        self,
        context,
        /,
        *,
        operation_key: str,
        case_count: int = 1,
        seed: int | None = None,
    ) -> OperationExecutionReport: ...


class OperationSmokeAgent:
    """Improve generators using whole-batch evidence and whole-batch validation."""

    def __init__(
        self,
        *,
        config_catalog: GeneratorConfigCatalog,
        batch_runner: OperationBatchRunner,
        diagnoser: OperationSmokeDiagnoser,
        reference_values: ReferenceValueProvider,
        tracing_runtime: TracingRuntime | None = None,
    ) -> None:
        self.config_catalog = config_catalog
        self.batch_runner = batch_runner
        self.diagnoser = diagnoser
        self.reference_values = reference_values
        self.tracing_runtime = tracing_runtime or TracingRuntime.disabled()

    def run(
        self,
        context,
        request: OperationSmokeRequest,
    ) -> OperationSmokeResult:
        with self.tracing_runtime.span(
            "OperationSmokeAgent.run",
            kind="AGENT",
            input_value={
                "operation_key": request.operation_key,
                "case_count": request.case_count,
                "success_rate_threshold": request.success_rate_threshold,
                "max_feedback_rounds": request.max_feedback_rounds,
                "max_planning_outputs": request.max_planning_outputs,
                "max_http_tool_rounds": request.max_http_tool_rounds,
                "seed": request.seed,
            },
            attributes={
                "restscope.operation.key": request.operation_key,
                "restscope.smoke.case_count": request.case_count,
                "restscope.smoke.success_rate_threshold": (
                    request.success_rate_threshold
                ),
                "restscope.smoke.max_feedback_rounds": (
                    request.max_feedback_rounds
                ),
                "restscope.smoke.max_planning_outputs": (
                    request.max_planning_outputs
                ),
                "restscope.smoke.max_http_tool_rounds": (
                    request.max_http_tool_rounds
                ),
            },
        ) as span:
            result = self._run(context, request)
            span.set_output(
                {
                    "status": result.status,
                    "success_rate": result.success_rate,
                    "batch_run_ids": [
                        report.run_id for report in result.batch_reports
                    ],
                    "diagnosis_count": len(result.diagnoses),
                    "failure_kind": result.failure_kind,
                }
            )
            span.set_attribute("restscope.smoke.status", result.status)
            span.set_attribute(
                "restscope.smoke.success_rate",
                result.success_rate,
            )
            span.set_attribute(
                "restscope.smoke.batch_count",
                len(result.batch_reports),
            )
            span.set_attribute(
                "restscope.smoke.diagnosis_count",
                len(result.diagnoses),
            )
            if result.failure_kind is not None:
                span.set_attribute(
                    "restscope.smoke.failure_kind",
                    result.failure_kind,
                )
            if result.status == "errored":
                span.mark_error("Operation Smoke returned an errored result")
            return result

    def _run(
        self,
        context,
        request: OperationSmokeRequest,
    ) -> OperationSmokeResult:
        current = self.config_catalog.get_operation(request.operation_key)
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
                diagnoses=[],
                failure_kind="unsupported_operation",
            )
        reports: list[OperationExecutionReport] = []
        diagnoses: list[PlanSolveDiagnosisResult] = []
        success_rate = 0.0
        seed = request.seed if request.seed is not None else secrets.randbits(63)
        current_history = self.config_catalog.get_revision(
            request.operation_key,
            current.revision,
        )
        feedback_rounds = (
            1
            if current_history is not None
            and current_history.lifecycle == "candidate"
            else 0
        )

        try:
            while True:
                _assert_reference_invariants(
                    current,
                    self.reference_values,
                )

                report, private_case_evidence = _run_smoke_batch(
                    self.batch_runner,
                    context=context,
                    operation_key=request.operation_key,
                    case_count=request.case_count,
                    seed=seed,
                )
                reports.append(report)
                if report.config_revision != current.revision:
                    raise RuntimeError(
                        "Batch report revision does not match the tested config"
                    )
                evaluation = _batch_evaluation(
                    report,
                    threshold=request.success_rate_threshold,
                )
                success_rate = float(evaluation["success_rate"])
                current_history = self.config_catalog.get_revision(
                    request.operation_key,
                    current.revision,
                )
                is_candidate = (
                    current_history is not None
                    and current_history.lifecycle == "candidate"
                )
                if success_rate >= request.success_rate_threshold:
                    if is_candidate:
                        self.config_catalog.accept_candidate(
                            operation_key=request.operation_key,
                            candidate_revision=current.revision,
                            evaluation=evaluation,
                        )
                    return self._result(
                        status="passed",
                        request=request,
                        current=current,
                        success_rate=success_rate,
                        reports=reports,
                        diagnoses=diagnoses,
                    )

                if feedback_rounds >= request.max_feedback_rounds:
                    if is_candidate:
                        current = self.config_catalog.reject_candidate_and_rollback(
                            operation_key=request.operation_key,
                            candidate_revision=current.revision,
                            evaluation=evaluation,
                        )
                    return self._result(
                        status="retry",
                        request=request,
                        current=current,
                        success_rate=success_rate,
                        reports=reports,
                        diagnoses=diagnoses,
                        failure_kind="threshold_exhausted",
                    )

                diagnosis = self.diagnoser.diagnose(
                    report=report,
                    config=current,
                    reference_option_provider=lambda input_node_ids: (
                        _available_reference_options(
                            self.reference_values,
                            context=context,
                            config=current,
                            input_node_ids=input_node_ids,
                        )
                    ),
                    private_case_evidence=private_case_evidence,
                    previous_experiment=(
                        _previous_experiment_summary(
                            diagnoses[-1],
                            evaluation=evaluation,
                            config=current,
                        )
                        if is_candidate and diagnoses
                        else None
                    ),
                    max_planning_outputs=request.max_planning_outputs,
                    max_http_tool_rounds=request.max_http_tool_rounds,
                )
                if diagnosis.status != "patch_ready":
                    diagnoses.append(diagnosis)
                    if is_candidate:
                        current = self.config_catalog.reject_candidate_and_rollback(
                            operation_key=request.operation_key,
                            candidate_revision=current.revision,
                            evaluation=evaluation,
                        )
                    return self._result(
                        status="retry",
                        request=request,
                        current=current,
                        success_rate=success_rate,
                        reports=reports,
                        diagnoses=diagnoses,
                        failure_kind=(
                            "no_parameter_issue"
                            if diagnosis.status == "no_parameter_issue"
                            else "diagnosis_inconclusive"
                        ),
                    )

                assert diagnosis.patch is not None
                updates = _prepare_reference_updates(
                    self.reference_values,
                    context=context,
                    config=current,
                    updates=diagnosis.patch.updates,
                    selected_reference_options=(
                        diagnosis.selected_reference_options
                    ),
                )
                diagnosis = diagnosis.model_copy(
                    update={
                        "patch": diagnosis.patch.model_copy(
                            update={"updates": updates}
                        )
                    }
                )
                diagnoses.append(diagnosis)
                if is_candidate:
                    current = self.config_catalog.reject_candidate_and_rollback(
                        operation_key=request.operation_key,
                        candidate_revision=current.revision,
                        evaluation=evaluation,
                    )
                current = self.config_catalog.stage_candidate(
                    operation_key=request.operation_key,
                    expected_revision=current.revision,
                    updates=updates,
                    hypothesis={"kind": "operation_smoke_joint_patch"},
                )
                feedback_rounds += 1
        except SQLAlchemyError:
            # Database availability is a shared-run invariant. Let Supervisor
            # stop the run as a global technical error instead of retrying one
            # operation against the same unavailable catalog.
            raise
        except Exception as exc:
            current = self._rollback_pending_candidate(
                current,
                reports=reports,
                threshold=request.success_rate_threshold,
            )
            return self._result(
                status="errored",
                request=request,
                current=current,
                success_rate=success_rate,
                reports=reports,
                diagnoses=diagnoses,
                failure_kind="operation_error",
                error={
                    "type": type(exc).__name__,
                    "message": str(exc),
                },
            )

    def _rollback_pending_candidate(
        self,
        current: OperationGeneratorConfig,
        *,
        reports: list[OperationExecutionReport],
        threshold: float,
    ) -> OperationGeneratorConfig:
        history = self.config_catalog.get_revision(
            current.operation_key,
            current.revision,
        )
        if history is None or history.lifecycle != "candidate":
            return current
        evaluation = (
            _batch_evaluation(reports[-1], threshold=threshold)
            if reports and reports[-1].config_revision == current.revision
            else {"stop_reason": "technical_error"}
        )
        return self.config_catalog.reject_candidate_and_rollback(
            operation_key=current.operation_key,
            candidate_revision=current.revision,
            evaluation=evaluation,
        )

    @staticmethod
    def _result(
        *,
        status: str,
        request: OperationSmokeRequest,
        current: OperationGeneratorConfig,
        success_rate: float,
        reports: list[OperationExecutionReport],
        diagnoses: list[PlanSolveDiagnosisResult],
        failure_kind: str | None = None,
        error: dict[str, str] | None = None,
    ) -> OperationSmokeResult:
        return OperationSmokeResult(
            status=status,
            operation_key=request.operation_key,
            success_rate=success_rate,
            required_success_rate=request.success_rate_threshold,
            active_config_revision=current.revision,
            batch_reports=reports,
            diagnoses=diagnoses,
            failure_kind=failure_kind,
            error=error,
        )


def _batch_evaluation(
    report: OperationExecutionReport,
    *,
    threshold: float,
) -> dict:
    success_count = sum(
        count
        for status, count in report.status_code_counts.items()
        if status.isdigit() and 200 <= int(status) < 300
    )
    case_count = sum(report.status_code_counts.values()) + report.error_count
    success_rate = success_count / case_count if case_count else 0.0
    return {
        "case_count": case_count,
        "success_2xx_count": success_count,
        "success_rate": success_rate,
        "required_threshold": threshold,
        "run_id": report.run_id,
    }


def _assert_reference_invariants(
    config: OperationGeneratorConfig,
    reference_values: ReferenceValueProvider,
) -> None:
    for item in config.configs:
        strategy = item.strategy
        if isinstance(strategy, ResourceIdentifierGenerator):
            name = strategy.resource
        elif isinstance(strategy, ResponseValueGenerator):
            name = strategy.value_name
        else:
            continue
        if reference_values.values_for(strategy):
            continue
        raise RuntimeError(
            "Reference generator invariant violated: "
            f"{item.input_node_id} uses empty {strategy.type} pool {name!r}"
        )


def _available_reference_options(
    reference_values: ReferenceValueProvider,
    *,
    context,
    config: OperationGeneratorConfig,
    input_node_ids: set[str],
) -> list[AvailableReferenceOption]:
    available = getattr(reference_values, "available_options", None)
    if not callable(available):
        return []
    return list(
        available(
            ir=getattr(context, "ir", None),
            config=config,
            input_node_ids=input_node_ids,
        )
    )


def _prepare_reference_updates(
    reference_values: ReferenceValueProvider,
    *,
    context,
    config: OperationGeneratorConfig,
    updates,
    selected_reference_options,
):
    prepare = getattr(reference_values, "prepare_updates", None)
    if not callable(prepare):
        prepared = updates
    else:
        prepared = prepare(
            ir=getattr(context, "ir", None),
            config=config,
            updates=updates,
            selected_reference_options=selected_reference_options,
        )
    for update in prepared:
        strategy = update.strategy
        if not isinstance(
            strategy,
            (ResourceIdentifierGenerator, ResponseValueGenerator),
        ):
            continue
        if not reference_values.values_for(strategy):
            raise RuntimeError(
                "Selected reference generator pool is empty for "
                f"{update.input_node_id}"
            )
    return prepared


def _run_smoke_batch(
    runner: OperationBatchRunner,
    *,
    context,
    operation_key: str,
    case_count: int,
    seed: int,
) -> tuple[OperationExecutionReport, dict[str, object]]:
    run_for_smoke = getattr(runner, "run_operation_for_smoke", None)
    if not callable(run_for_smoke):
        return (
            runner.run_operation(
                context,
                operation_key=operation_key,
                case_count=case_count,
                seed=seed,
            ),
            {},
        )
    outcome = run_for_smoke(
        context,
        operation_key=operation_key,
        case_count=case_count,
        seed=seed,
    )
    return (
        outcome.report,
        {item.case_id: item for item in outcome.case_evidence},
    )


def _previous_experiment_summary(
    diagnosis: PlanSolveDiagnosisResult,
    *,
    evaluation: dict,
    config: OperationGeneratorConfig,
) -> dict:
    semantic_inputs = build_semantic_input_map(config)
    changes = []
    if diagnosis.patch is not None:
        for update in diagnosis.patch.updates:
            handle = semantic_inputs.handle_by_node.get(update.input_node_id)
            if handle is None:
                continue
            changes.append(
                {
                    "input": handle,
                    "inclusion_probability": update.inclusion_probability,
                    "generation": (
                        update.strategy.model_dump(mode="json")
                        if update.strategy is not None
                        else None
                    ),
                }
            )
    return {
        "diagnosis_status": diagnosis.status,
        "termination_reason": diagnosis.termination_reason,
        "covered_item_count": len(diagnosis.covered_item_ids),
        "deferred_item_count": len(diagnosis.deferred_items),
        "generator_changes": changes,
        "candidate_success_rate": evaluation.get("success_rate"),
        "candidate_case_count": evaluation.get("case_count"),
    }
