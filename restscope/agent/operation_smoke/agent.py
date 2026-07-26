"""Bounded batch-feedback loop for one operation's smoke test."""

from __future__ import annotations

import secrets
from typing import Protocol

from sqlalchemy.exc import SQLAlchemyError

from restscope.observability import TracingRuntime
from restscope.testing import (
    ConstraintSet,
    GeneratorConfigCatalog,
    OperationExecutionReport,
    OperationGeneratorConfig,
    ReferenceValueProvider,
    ResourceIdentifierGenerator,
    ResponseValueGenerator,
)
from restscope.testing.generation import generate_test_case

from .diagnosis import OperationSmokeDiagnoser
from .evidence import build_semantic_input_map
from .schemas import (
    AvailableReferenceOption,
    CompiledConstraintPatch,
    GeneratorPatchDraft,
    OperationSmokeRequest,
    OperationSmokeResult,
    PatchValidationSummary,
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
        pending_diagnosis: PlanSolveDiagnosisResult | None = None
        pending_baseline_report: OperationExecutionReport | None = None
        previous_experiment: dict | None = None
        active_constraints: dict[str, CompiledConstraintPatch] = {}

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
                    constraints=_combined_constraints(
                        [
                            *active_constraints.values(),
                            *(
                                pending_diagnosis.patch.constraints
                                if pending_diagnosis is not None
                                and pending_diagnosis.patch is not None
                                else []
                            ),
                        ]
                    ),
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
                if pending_diagnosis is not None:
                    assert pending_diagnosis.patch is not None
                    has_generator_candidate = bool(
                        pending_diagnosis.patch.updates
                    )
                    if has_generator_candidate and (
                        current_history is None
                        or current_history.lifecycle != "candidate"
                    ):
                        raise RuntimeError(
                            "Pending generator patch has no candidate revision"
                        )
                    validation = self.diagnoser.validate_patch(
                        report=report,
                        config=current,
                        diagnosis=pending_diagnosis,
                        private_case_evidence=private_case_evidence,
                        baseline_report=pending_baseline_report,
                    )
                    pending_diagnosis = pending_diagnosis.model_copy(
                        update={"patch_validation": validation}
                    )
                    diagnoses[-1] = pending_diagnosis
                    success_override = (
                        success_rate >= request.success_rate_threshold
                    )
                    candidate_evaluation = _candidate_evaluation(
                        evaluation,
                        validation=validation,
                        change_count=len(
                            pending_diagnosis.patch.attributions
                        )
                        + len(
                            pending_diagnosis.patch.constraints
                        ),
                        success_override=success_override,
                    )
                    candidate_config = current
                    accepted_constraint_ids = (
                        {
                            constraint.constraint_id
                            for constraint in (
                                pending_diagnosis.patch.constraints
                            )
                        }
                        if success_override
                        else set(validation.accepted_constraint_ids)
                    )
                    for constraint in pending_diagnosis.patch.constraints:
                        if constraint.constraint_id in accepted_constraint_ids:
                            active_constraints[constraint.constraint_id] = (
                                constraint
                            )
                    if has_generator_candidate and success_override:
                        current = self.config_catalog.finalize_candidate(
                            operation_key=request.operation_key,
                            candidate_revision=current.revision,
                            accepted_input_node_ids={
                                attribution.input_node_id
                                for attribution in (
                                    pending_diagnosis.patch.attributions
                                    if pending_diagnosis.patch is not None
                                    else []
                                )
                            },
                            evaluation=candidate_evaluation,
                        )
                    elif has_generator_candidate:
                        current = self.config_catalog.finalize_candidate(
                            operation_key=request.operation_key,
                            candidate_revision=current.revision,
                            accepted_input_node_ids=set(
                                validation.accepted_input_node_ids
                            ),
                            evaluation=candidate_evaluation,
                        )
                    previous_experiment = _previous_experiment_summary(
                        pending_diagnosis,
                        evaluation=candidate_evaluation,
                        config=candidate_config,
                    )
                    pending_diagnosis = None
                    pending_baseline_report = None
                    if success_override:
                        return self._result(
                            status="passed",
                            request=request,
                            current=current,
                            success_rate=success_rate,
                            reports=reports,
                            diagnoses=diagnoses,
                        )
                    if feedback_rounds >= request.max_feedback_rounds:
                        return self._result(
                            status="retry",
                            request=request,
                            current=current,
                            success_rate=success_rate,
                            reports=reports,
                            diagnoses=diagnoses,
                            failure_kind="threshold_exhausted",
                        )
                elif success_rate >= request.success_rate_threshold:
                    return self._result(
                        status="passed",
                        request=request,
                        current=current,
                        success_rate=success_rate,
                        reports=reports,
                        diagnoses=diagnoses,
                    )

                elif feedback_rounds >= request.max_feedback_rounds:
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
                    previous_experiment=previous_experiment,
                    patch_preflight=lambda patch: _preflight_patch(
                        catalog=self.config_catalog,
                        reference_values=self.reference_values,
                        current=current,
                        patch=patch,
                        active_constraints=list(
                            active_constraints.values()
                        ),
                        case_count=request.case_count,
                        seed=seed,
                    ),
                    max_planning_outputs=request.max_planning_outputs,
                    max_http_tool_rounds=request.max_http_tool_rounds,
                )
                if diagnosis.status != "patch_ready":
                    diagnoses.append(diagnosis)
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
                        "patch": type(diagnosis.patch)(
                            updates=updates,
                            attributions=diagnosis.patch.attributions,
                            constraints=diagnosis.patch.constraints,
                        )
                    }
                )
                diagnoses.append(diagnosis)
                if updates:
                    current = self.config_catalog.stage_candidate(
                        operation_key=request.operation_key,
                        expected_revision=current.revision,
                        updates=updates,
                        hypothesis={"kind": "operation_smoke_joint_patch"},
                    )
                pending_diagnosis = diagnosis
                pending_baseline_report = report
                feedback_rounds += 1
        except SQLAlchemyError:
            # Database availability is a shared-run invariant. Let Supervisor
            # stop the run as a global technical error instead of retrying one
            # operation against the same unavailable catalog.
            raise
        except Exception as exc:
            current = self._discard_pending_candidate(
                current,
                reports=reports,
                threshold=request.success_rate_threshold,
                candidate_change_count=(
                    len(pending_diagnosis.patch.attributions)
                    + len(pending_diagnosis.patch.constraints)
                    if pending_diagnosis is not None
                    and pending_diagnosis.patch is not None
                    else 0
                ),
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

    def _discard_pending_candidate(
        self,
        current: OperationGeneratorConfig,
        *,
        reports: list[OperationExecutionReport],
        threshold: float,
        candidate_change_count: int,
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
        evaluation = {
            **evaluation,
            "validation_status": "technical_error",
            "accepted_change_count": 0,
            "rejected_change_count": candidate_change_count,
        }
        return self.config_catalog.finalize_candidate(
            operation_key=current.operation_key,
            candidate_revision=current.revision,
            accepted_input_node_ids=set(),
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


def _candidate_evaluation(
    evaluation: dict,
    *,
    validation: PatchValidationSummary,
    change_count: int,
    success_override: bool,
) -> dict:
    accepted_count = (
        change_count
        if success_override
        else (
            len(validation.accepted_input_node_ids)
            + len(validation.accepted_constraint_ids)
        )
    )
    if success_override:
        validation_status = "success_threshold_override"
    elif accepted_count == change_count:
        validation_status = "accepted"
    elif accepted_count:
        validation_status = "partial"
    else:
        validation_status = "rejected"
    return {
        **evaluation,
        "validation_status": validation_status,
        "accepted_change_count": accepted_count,
        "rejected_change_count": change_count - accepted_count,
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


def _combined_constraints(
    patches: list[CompiledConstraintPatch],
) -> ConstraintSet | None:
    unique_patches = {patch.constraint_id: patch for patch in patches}
    expressions = [
        expression
        for patch in unique_patches.values()
        for expression in patch.constraint.constraints
    ]
    if not expressions:
        return None
    return ConstraintSet(constraints=expressions)


def _preflight_patch(
    *,
    catalog: GeneratorConfigCatalog,
    reference_values: ReferenceValueProvider,
    current: OperationGeneratorConfig,
    patch: GeneratorPatchDraft,
    active_constraints: list[CompiledConstraintPatch],
    case_count: int,
    seed: int,
) -> list[str]:
    """Generate the complete experimental batch without mutating runtime state."""

    try:
        preview = catalog.preview_candidate(
            operation_key=current.operation_key,
            updates=patch.updates,
        )
        constraints = _combined_constraints(
            [*active_constraints, *patch.constraints]
        )
        for case_index in range(case_count):
            generate_test_case(
                preview.snapshot,
                preview,
                run_seed=seed,
                case_index=case_index,
                reference_values=reference_values,
                constraints=constraints,
            )
    except ValueError as exc:
        return [
            "Candidate patch cannot generate a complete batch "
            f"({type(exc).__name__})."
        ]
    return []


def _run_smoke_batch(
    runner: OperationBatchRunner,
    *,
    context,
    operation_key: str,
    case_count: int,
    seed: int,
    constraints: ConstraintSet | None,
) -> tuple[OperationExecutionReport, dict[str, object]]:
    run_for_smoke = getattr(runner, "run_operation_for_smoke", None)
    if not callable(run_for_smoke):
        if constraints is not None:
            raise RuntimeError(
                "The batch runner does not support runtime constraints"
            )
        return (
            runner.run_operation(
                context,
                operation_key=operation_key,
                case_count=case_count,
                seed=seed,
            ),
            {},
        )
    arguments = {
        "operation_key": operation_key,
        "case_count": case_count,
        "seed": seed,
    }
    if constraints is not None:
        arguments["constraints"] = constraints
    outcome = run_for_smoke(context, **arguments)
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
    accepted_input_node_ids = set(
        diagnosis.patch_validation.accepted_input_node_ids
        if diagnosis.patch_validation is not None
        else []
    )
    accepted_constraint_ids = set(
        diagnosis.patch_validation.accepted_constraint_ids
        if diagnosis.patch_validation is not None
        else []
    )
    changes = []
    constraints = []
    if diagnosis.patch is not None:
        for update in diagnosis.patch.updates:
            handle = semantic_inputs.handle_by_node.get(update.input_node_id)
            if handle is None:
                continue
            change = {
                "input": handle,
                "inclusion_probability": update.inclusion_probability,
                "generation": (
                    update.strategy.model_dump(mode="json")
                    if update.strategy is not None
                    else None
                ),
            }
            if diagnosis.patch_validation is not None:
                change["outcome"] = (
                    "accepted"
                    if update.input_node_id in accepted_input_node_ids
                    else "removed"
                )
            changes.append(change)
        for constraint in diagnosis.patch.constraints:
            summary = {"kind": constraint.kind}
            if diagnosis.patch_validation is not None:
                summary["outcome"] = (
                    "accepted"
                    if constraint.constraint_id in accepted_constraint_ids
                    else "removed"
                )
            constraints.append(summary)
    summary = {
        "diagnosis_status": diagnosis.status,
        "termination_reason": diagnosis.termination_reason,
        "covered_item_count": len(diagnosis.covered_item_ids),
        "deferred_item_count": len(diagnosis.deferred_items),
        "generator_changes": changes,
        "constraints": constraints,
        "candidate_success_rate": evaluation.get("success_rate"),
        "candidate_case_count": evaluation.get("case_count"),
    }
    if diagnosis.patch_validation is not None:
        summary.update(
            {
                "accepted_change_count": evaluation.get(
                    "accepted_change_count",
                    len(accepted_input_node_ids)
                    + len(accepted_constraint_ids),
                ),
                "removed_change_count": evaluation.get(
                    "rejected_change_count",
                    len(changes)
                    + len(constraints)
                    - len(accepted_input_node_ids)
                    - len(accepted_constraint_ids),
                ),
                "evidence_note": (
                    "This candidate batch used the complete experimental patch. "
                    "Effects marked removed are no longer active in this Smoke "
                    "run."
                ),
            }
        )
    return summary
