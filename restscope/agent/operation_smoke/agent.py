"""Bounded batch-feedback loop for one operation's smoke test."""

from __future__ import annotations

import secrets
from typing import Protocol

from restscope.testing import (
    GeneratorConfigCatalog,
    OperationExecutionReport,
    OperationGeneratorConfig,
    ReferenceValueProvider,
    ResourceIdentifierGenerator,
    ResponseValueGenerator,
)

from .diagnosis import OperationSmokeDiagnoser
from .schemas import (
    OperationSmokeRequest,
    OperationSmokeResult,
    TwoRoundDiagnosisResult,
    WaitingReference,
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
    ) -> None:
        self.config_catalog = config_catalog
        self.batch_runner = batch_runner
        self.diagnoser = diagnoser
        self.reference_values = reference_values

    def run(
        self,
        context,
        request: OperationSmokeRequest,
    ) -> OperationSmokeResult:
        current = self.config_catalog.require_operation(request.operation_key)
        reports: list[OperationExecutionReport] = []
        diagnoses: list[TwoRoundDiagnosisResult] = []
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
                waiting = _missing_references(
                    current,
                    self.reference_values,
                )
                if waiting:
                    return self._result(
                        status="waiting",
                        request=request,
                        current=current,
                        success_rate=success_rate,
                        reports=reports,
                        diagnoses=diagnoses,
                        waiting=waiting,
                    )

                report = self.batch_runner.run_operation(
                    context,
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
                        status="failed",
                        request=request,
                        current=current,
                        success_rate=success_rate,
                        reports=reports,
                        diagnoses=diagnoses,
                    )

                diagnosis = self.diagnoser.diagnose(
                    report=report,
                    config=current,
                )
                if diagnosis.diagnosis.no_parameter_issue:
                    diagnoses.append(diagnosis)
                    if is_candidate:
                        current = self.config_catalog.reject_candidate_and_rollback(
                            operation_key=request.operation_key,
                            candidate_revision=current.revision,
                            evaluation=evaluation,
                        )
                    return self._result(
                        status="failed",
                        request=request,
                        current=current,
                        success_rate=success_rate,
                        reports=reports,
                        diagnoses=diagnoses,
                    )

                updates = _prepare_reference_updates(
                    self.reference_values,
                    context=context,
                    config=current,
                    updates=diagnosis.updates,
                )
                diagnosis = diagnosis.model_copy(update={"updates": updates})
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
                    hypothesis=diagnosis.diagnosis.model_dump(mode="json"),
                )
                feedback_rounds += 1
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
        diagnoses: list[TwoRoundDiagnosisResult],
        waiting: list[WaitingReference] | None = None,
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
            waiting_references=waiting or [],
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


def _missing_references(
    config: OperationGeneratorConfig,
    reference_values: ReferenceValueProvider,
) -> list[WaitingReference]:
    waiting: list[WaitingReference] = []
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
        waiting.append(
            WaitingReference(
                input_node_id=item.input_node_id,
                type=strategy.type,
                name=name,
            )
        )
    return waiting


def _prepare_reference_updates(
    reference_values: ReferenceValueProvider,
    *,
    context,
    config: OperationGeneratorConfig,
    updates,
):
    prepare = getattr(reference_values, "prepare_updates", None)
    if not callable(prepare):
        return updates
    return prepare(
        ir=context.ir,
        config=config,
        updates=updates,
    )
