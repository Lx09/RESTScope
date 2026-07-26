"""Bounded batch-feedback loop for one operation's smoke test."""

from __future__ import annotations

import secrets
from typing import Protocol

from sqlalchemy.exc import SQLAlchemyError

from restscope.agent.parameter_patch import (
    AvailableReferenceOption,
    CompiledConstraintPatch,
    ParameterPatchAgentFactory,
    PatchGroupFailure,
    ValidatedPatchGroup,
)
from restscope.observability import TracingRuntime
from restscope.testing import (
    ConstraintSet,
    GeneratorConfigCatalog,
    OperationExecutionReport,
    OperationGeneratorConfig,
    ReferenceValueProvider,
    ResourceIdentifierGenerator,
    ResponseValueGenerator,
    build_semantic_input_map,
    preview_generator_patch,
)

from .diagnosis import OperationSmokeDiagnoser
from .grouping import PatchGroupPlanner
from .schemas import (
    DeferredFailure,
    OperationSmokeRequest,
    OperationSmokeResult,
    PatchGroupRunSummary,
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
        group_planner: PatchGroupPlanner,
        patch_agent_factory: ParameterPatchAgentFactory,
        reference_values: ReferenceValueProvider,
        tracing_runtime: TracingRuntime | None = None,
    ) -> None:
        self.config_catalog = config_catalog
        self.batch_runner = batch_runner
        self.diagnoser = diagnoser
        self.group_planner = group_planner
        self.patch_agent_factory = patch_agent_factory
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
                "max_diagnosis_outputs_per_failure": (
                    request.max_diagnosis_outputs_per_failure
                ),
                "max_patch_attempts": request.max_patch_attempts,
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
                "restscope.smoke.max_diagnosis_outputs_per_failure": (
                    request.max_diagnosis_outputs_per_failure
                ),
                "restscope.smoke.max_patch_attempts": (
                    request.max_patch_attempts
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
        current = self.config_catalog.recover_interrupted_candidate(
            request.operation_key
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
        feedback_rounds = 0
        active_constraints: dict[str, CompiledConstraintPatch] = {}
        pending_change_count = 0

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
                        list(active_constraints.values())
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
                if success_rate >= request.success_rate_threshold:
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

                diagnosis = self.diagnoser.diagnose(
                    report=report,
                    config=current,
                    private_case_evidence=private_case_evidence,
                    max_diagnosis_outputs_per_failure=(
                        request.max_diagnosis_outputs_per_failure
                    ),
                )
                diagnoses.append(diagnosis)
                if diagnosis.status != "actionable":
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

                grouping = self.group_planner.group(
                    actionable_failures=diagnosis.actionable_failures,
                    config=current,
                )
                diagnosis = _defer_actionable_items(
                    diagnosis,
                    {
                        item_id: "patch_grouping_deferred"
                        for item_id in grouping.deferred_item_ids
                    },
                )
                diagnoses[-1] = diagnosis
                if grouping.status != "grouped":
                    return self._result(
                        status="retry",
                        request=request,
                        current=current,
                        success_rate=success_rate,
                        reports=reports,
                        diagnoses=diagnoses,
                        failure_kind="diagnosis_inconclusive",
                    )

                successful_groups: list[ValidatedPatchGroup] = []
                patch_group_runs: list[PatchGroupRunSummary] = []
                failed_item_reasons: dict[str, str] = {}
                provisional_config = current
                provisional_constraints = list(active_constraints.values())
                semantic = build_semantic_input_map(current)
                for task in grouping.tasks:
                    input_node_ids = {
                        semantic.node_by_handle[input_handle]
                        for input_handle in task.inputs
                    }
                    reference_options = _available_reference_options(
                        self.reference_values,
                        context=context,
                        config=provisional_config,
                        input_node_ids=input_node_ids,
                    )
                    outcome = self.patch_agent_factory.create().run(
                        task=task,
                        config=provisional_config,
                        active_constraints=provisional_constraints,
                        reference_values=self.reference_values,
                        reference_options=reference_options,
                        max_attempts=request.max_patch_attempts,
                    )
                    if isinstance(outcome, PatchGroupFailure):
                        failed_item_reasons.update(
                            {
                                item_id: f"patch_group_{outcome.reason}"
                                for item_id in outcome.item_ids
                            }
                        )
                        patch_group_runs.append(
                            PatchGroupRunSummary(
                                group_id=outcome.group_id,
                                item_ids=outcome.item_ids,
                                root_failure_refs=outcome.root_failure_refs,
                                status="failed",
                                attempts=outcome.attempts,
                                failure_reason=outcome.reason,
                            )
                        )
                        continue
                    patch_group_runs.append(
                        PatchGroupRunSummary(
                            group_id=outcome.group_id,
                            item_ids=outcome.item_ids,
                            root_failure_refs=outcome.root_failure_refs,
                            status="validated",
                            attempts=outcome.attempts,
                        )
                    )
                    successful_groups.append(outcome)
                    provisional_config = preview_generator_patch(
                        provisional_config,
                        outcome.patch.updates,
                    )
                    provisional_constraints.extend(
                        outcome.patch.constraints
                    )

                diagnosis = diagnosis.model_copy(
                    update={"patch_group_runs": patch_group_runs}
                )
                diagnosis = _defer_actionable_items(
                    diagnosis,
                    failed_item_reasons,
                )
                diagnoses[-1] = diagnosis
                if not successful_groups:
                    return self._result(
                        status="retry",
                        request=request,
                        current=current,
                        success_rate=success_rate,
                        reports=reports,
                        diagnoses=diagnoses,
                        failure_kind="diagnosis_inconclusive",
                    )

                updates = [
                    update
                    for group in successful_groups
                    for update in group.patch.updates
                ]
                updates = _prepare_reference_updates(
                    self.reference_values,
                    context=context,
                    config=current,
                    updates=updates,
                    selected_reference_options=[
                        option
                        for group in successful_groups
                        for option in group.patch.selected_reference_options
                    ],
                )
                candidate_constraints = [
                    constraint
                    for group in successful_groups
                    for constraint in group.patch.constraints
                ]
                pending_change_count = len(updates) + len(
                    candidate_constraints
                )
                if updates:
                    current = self.config_catalog.stage_candidate(
                        operation_key=request.operation_key,
                        expected_revision=current.revision,
                        updates=updates,
                        hypothesis={
                            "kind": "operation_smoke_parameter_patch_groups",
                            "group_count": len(successful_groups),
                        },
                    )

                candidate_report, _ = _run_smoke_batch(
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
                        "Candidate report revision does not match tested config"
                    )
                candidate_evaluation = _batch_evaluation(
                    candidate_report,
                    threshold=request.success_rate_threshold,
                )
                success_rate = float(candidate_evaluation["success_rate"])
                validation = self.diagnoser.validate_effect(
                    baseline_report=report,
                    candidate_report=candidate_report,
                    diagnosis=diagnosis,
                    groups=successful_groups,
                )
                success_override = (
                    success_rate >= request.success_rate_threshold
                )
                if success_override:
                    validation = _accept_all_groups(
                        validation,
                        groups=successful_groups,
                    )
                diagnosis = diagnosis.model_copy(
                    update={"patch_validation": validation}
                )
                diagnoses[-1] = diagnosis
                accepted_group_ids = set(validation.accepted_group_ids)
                accepted_groups = [
                    group
                    for group in successful_groups
                    if group.group_id in accepted_group_ids
                ]
                for group in accepted_groups:
                    for constraint in group.patch.constraints:
                        active_constraints[constraint.constraint_id] = (
                            constraint
                        )
                accepted_input_ids = {
                    update.input_node_id
                    for group in accepted_groups
                    for update in group.patch.updates
                }
                if updates:
                    current = self.config_catalog.finalize_candidate(
                        operation_key=request.operation_key,
                        candidate_revision=current.revision,
                        accepted_input_node_ids=accepted_input_ids,
                        evaluation=_candidate_evaluation(
                            candidate_evaluation,
                            validation=validation,
                            change_count=pending_change_count,
                            success_override=success_override,
                        ),
                    )
                pending_change_count = 0
                feedback_rounds += 1
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
        except SQLAlchemyError:
            # Database availability is a shared-run invariant. Let Supervisor
            # stop the run as a global technical error instead of retrying one
            # operation against the same unavailable catalog. Best-effort
            # rollback keeps an already-staged candidate from becoming a later
            # run's baseline; run startup also recovers any rollback that could
            # not be written while the database was unavailable.
            try:
                current = self._discard_pending_candidate(
                    current,
                    reports=reports,
                    threshold=request.success_rate_threshold,
                    candidate_change_count=pending_change_count,
                )
            except Exception:
                pass
            raise
        except Exception as exc:
            current = self._discard_pending_candidate(
                current,
                reports=reports,
                threshold=request.success_rate_threshold,
                candidate_change_count=pending_change_count,
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


def _defer_actionable_items(
    diagnosis: PlanSolveDiagnosisResult,
    reasons_by_item_id: dict[str, str],
) -> PlanSolveDiagnosisResult:
    if not reasons_by_item_id:
        return diagnosis
    remaining = [
        item
        for item in diagnosis.actionable_failures
        if item.item_id not in reasons_by_item_id
    ]
    moved = [
        DeferredFailure(
            failure_ref=item.failure_ref,
            root_failure_refs=item.root_failure_refs,
            reason=reasons_by_item_id[item.item_id],
        )
        for item in diagnosis.actionable_failures
        if item.item_id in reasons_by_item_id
    ]
    if not moved:
        return diagnosis
    payload = diagnosis.model_dump(mode="json")
    payload.update(
        {
            "status": "actionable" if remaining else "inconclusive",
            "actionable_failures": [
                item.model_dump(mode="json") for item in remaining
            ],
            "deferred_failures": [
                item.model_dump(mode="json")
                for item in [*diagnosis.deferred_failures, *moved]
            ],
        }
    )
    return PlanSolveDiagnosisResult.model_validate(payload)


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


def _accept_all_groups(
    validation: PatchValidationSummary,
    *,
    groups: list[ValidatedPatchGroup],
) -> PatchValidationSummary:
    """Apply the existing global success-threshold acceptance override."""

    return validation.model_copy(
        update={
            "accepted_group_ids": [group.group_id for group in groups],
            "rejected_group_ids": [],
            "accepted_input_node_ids": list(
                dict.fromkeys(
                    update.input_node_id
                    for group in groups
                    for update in group.patch.updates
                )
            ),
            "rejected_input_node_ids": [],
            "accepted_constraint_ids": list(
                dict.fromkeys(
                    constraint.constraint_id
                    for group in groups
                    for constraint in group.patch.constraints
                )
            ),
            "rejected_constraint_ids": [],
        }
    )


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
