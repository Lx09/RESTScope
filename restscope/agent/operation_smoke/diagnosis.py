"""Bounded root-cause investigation for one failing Operation Smoke batch."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel

from restscope.agent.parameter_patch import ValidatedPatchGroup
from restscope.llm import (
    LLMClient,
    LLMMessage,
    LLMModelConfig,
    LLMRequest,
    LLMResponse,
    OutputValidator,
    ToolCall,
    ToolResult,
    ToolSpec,
)
from restscope.observability import TracingRuntime
from restscope.testing import (
    OperationExecutionReport,
    OperationGeneratorConfig,
)

from .evidence import (
    EvidenceJournal,
    build_effect_validation_payload,
)
from .planning import FailureDecision
from .prompts import (
    PatchValidationDecision,
    build_failure_investigation_prompt,
    build_patch_validation_decision_protocol,
)
from .schemas import (
    ActionableFailure,
    DeferredFailure,
    FailureHypothesis,
    FailureInvestigationState,
    FailureInvestigationSummary,
    PatchItemValidationSummary,
    PatchValidationSummary,
    PlanSolveDiagnosisResult,
)


MAX_DIAGNOSIS_FAILURES = 10
MAX_CONSECUTIVE_INVALID_OUTPUTS = 3
_MAX_REPAIR_ERRORS = 10
_OutputT = TypeVar("_OutputT", bound=BaseModel)


class HTTPProbe(Protocol):
    """
    Define the collaborator contract for httpprobe.

    Concrete implementations may vary while callers in the run-local Operation Smoke
    diagnosis and candidate workflow depend only on these declared operations.
    """
    def tool_spec(self, config: OperationGeneratorConfig) -> ToolSpec: ...

    def validate(
        self,
        *,
        config: OperationGeneratorConfig,
        tool_call: ToolCall,
    ) -> str | None: ...

    def execute(
        self,
        *,
        config: OperationGeneratorConfig,
        tool_call: ToolCall,
    ) -> ToolResult: ...


class OperationSmokeOutputError(RuntimeError):
    """A configured model cannot safely run one Smoke phase."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class OperationSmokeDiagnoser:
    """Investigate failures and later judge candidate effects.

    Diagnosis is a FIFO state machine over at most ten unique failure
    signatures. A model may use existing F/C/O evidence directly or declare a
    hypothesis and probe the current operation over HTTP. Deterministic code
    owns reference validity, tool scope, budgets, and observation ownership;
    the model owns the semantic judgment that evidence supports a root cause.
    """

    def __init__(
        self,
        *,
        client: LLMClient,
        planning_model: LLMModelConfig,
        effect_model: LLMModelConfig | None = None,
        http_probe: HTTPProbe | None = None,
        validator: OutputValidator | None = None,
        tracing_runtime: TracingRuntime | None = None,
    ) -> None:
        self.client = client
        self.planning_model = planning_model
        self.effect_model = effect_model or planning_model.model_copy(
            update={"role": "operation_smoke_effect_validation"}
        )
        self.http_probe = http_probe
        self.validator = validator or OutputValidator()
        self.tracing_runtime = tracing_runtime or TracingRuntime.disabled()

    def diagnose(
        self,
        *,
        report: OperationExecutionReport,
        config: OperationGeneratorConfig,
        private_case_evidence: Mapping[str, Any] | None = None,
        max_diagnosis_outputs_per_failure: int = 20,
    ) -> PlanSolveDiagnosisResult:
        """Investigate one failed batch and return findings, never a concrete patch."""
        if not 1 <= max_diagnosis_outputs_per_failure <= 20:
            raise ValueError(
                "max_diagnosis_outputs_per_failure must be between 1 and 20"
            )
        with self.tracing_runtime.span(
            "OperationSmokeDiagnoser.diagnose",
            kind="AGENT",
            input_value={
                "operation_key": report.operation_key,
                "run_id": report.run_id,
                "failure_count": len(
                    report.failure_report.unique_failure_messages
                ),
                "max_diagnosis_outputs_per_failure": (
                    max_diagnosis_outputs_per_failure
                ),
            },
            attributes={
                "restscope.operation.key": report.operation_key,
                "restscope.test.run_id": report.run_id,
                "restscope.smoke.max_diagnosis_outputs_per_failure": (
                    max_diagnosis_outputs_per_failure
                ),
            },
        ) as span:
            result = self._diagnose_failures(
                report=report,
                config=config,
                private_case_evidence=private_case_evidence,
                max_outputs_per_failure=(
                    max_diagnosis_outputs_per_failure
                ),
            )
            span.set_output(
                {
                    "status": result.status,
                    "termination_reason": result.termination_reason,
                    "valid_outputs": result.valid_outputs,
                    "invalid_outputs": result.invalid_outputs,
                    "http_tool_calls": result.http_tool_calls,
                    "actionable_count": len(result.actionable_failures),
                    "deferred_count": len(result.deferred_failures),
                }
            )
            for name, value in (
                ("restscope.smoke.diagnosis_status", result.status),
                (
                    "restscope.smoke.termination_reason",
                    result.termination_reason,
                ),
                (
                    "restscope.smoke.diagnosis_valid_outputs",
                    result.valid_outputs,
                ),
                (
                    "restscope.smoke.diagnosis_invalid_outputs",
                    result.invalid_outputs,
                ),
                (
                    "restscope.smoke.diagnosis_http_tool_calls",
                    result.http_tool_calls,
                ),
                (
                    "restscope.smoke.actionable_count",
                    len(result.actionable_failures),
                ),
                (
                    "restscope.smoke.deferred_count",
                    len(result.deferred_failures),
                ),
            ):
                span.set_attribute(name, value)
            return result

    def validate_effect(
        self,
        *,
        baseline_report: OperationExecutionReport,
        candidate_report: OperationExecutionReport,
        baseline_private_case_evidence: Mapping[str, Any] | None = None,
        candidate_private_case_evidence: Mapping[str, Any] | None = None,
        diagnosis: PlanSolveDiagnosisResult,
        groups: list[ValidatedPatchGroup],
    ) -> PatchValidationSummary:
        """Judge only whether initial failures changed in the candidate batch."""

        if not self.effect_model.enabled:
            raise OperationSmokeOutputError(
                "operation_smoke_effect_model_not_configured",
                "The Operation Smoke effect validation model is not configured",
            )
        if baseline_report.operation_key != candidate_report.operation_key:
            raise OperationSmokeOutputError(
                "operation_smoke_baseline_report_mismatch",
                "Baseline and candidate reports identify different operations",
            )
        if baseline_report.seed != candidate_report.seed:
            raise OperationSmokeOutputError(
                "operation_smoke_baseline_seed_mismatch",
                "Baseline and candidate reports must use the same seed",
            )
        with self.tracing_runtime.span(
            "OperationSmokeDiagnoser.validate_effect",
            kind="AGENT",
            input_value={
                "operation_key": baseline_report.operation_key,
                "baseline_run_id": baseline_report.run_id,
                "candidate_run_id": candidate_report.run_id,
                "group_count": len(groups),
            },
            attributes={
                "restscope.operation.key": baseline_report.operation_key,
                "restscope.smoke.effect_group_count": len(groups),
            },
        ) as span:
            result = self._validate_effect(
                baseline_report=baseline_report,
                candidate_report=candidate_report,
                baseline_private_case_evidence=(
                    baseline_private_case_evidence
                ),
                candidate_private_case_evidence=(
                    candidate_private_case_evidence
                ),
                diagnosis=diagnosis,
                groups=groups,
            )
            span.set_output(
                {
                    "accepted_group_count": len(result.accepted_group_ids),
                    "rejected_group_count": len(result.rejected_group_ids),
                    "resolved_failure_count": len(
                        result.accepted_item_ids
                    ),
                }
            )
            span.set_attribute(
                "restscope.smoke.effect_accepted_group_count",
                len(result.accepted_group_ids),
            )
            span.set_attribute(
                "restscope.smoke.effect_rejected_group_count",
                len(result.rejected_group_ids),
            )
            return result

    def _validate_effect(
        self,
        *,
        baseline_report: OperationExecutionReport,
        candidate_report: OperationExecutionReport,
        baseline_private_case_evidence: Mapping[str, Any] | None,
        candidate_private_case_evidence: Mapping[str, Any] | None,
        diagnosis: PlanSolveDiagnosisResult,
        groups: list[ValidatedPatchGroup],
    ) -> PatchValidationSummary:
        """
        Validate effect for the run-local Operation Smoke diagnosis and candidate
        workflow.

        This private helper keeps one transformation or policy decision explicit so the
        surrounding orchestration remains readable.
        """
        target_refs = [
            f"F{index}"
            for index, _ in enumerate(
                baseline_report.failure_report.unique_failure_messages,
                start=1,
            )
        ]
        baseline_failures = [
            {
                "ref": f"F{index}",
                "message": failure.message,
                "case_refs": failure.case_ids,
            }
            for index, failure in enumerate(
                baseline_report.failure_report.unique_failure_messages,
                start=1,
            )
        ]
        candidate_failures = [
            {
                "ref": f"CF{index}",
                "message": failure.message,
                "case_refs": failure.case_ids,
            }
            for index, failure in enumerate(
                candidate_report.failure_report.unique_failure_messages,
                start=1,
            )
        ]
        candidate_failure_refs = [
            failure["ref"] for failure in candidate_failures
        ]
        actionables = [
            {
                "item_id": item.item_id,
                "failure_ref": item.failure_ref,
                "root_failure_refs": item.root_failure_refs,
                "cause": item.cause,
                "desired_behaviors": [
                    {
                        "input": solution.input,
                        "desired_behavior": solution.desired_behavior,
                    }
                    for solution in item.solutions
                ],
            }
            for item in diagnosis.actionable_failures
            if any(root in target_refs for root in item.root_failure_refs)
        ]
        protocol = build_patch_validation_decision_protocol(
            target_refs=target_refs,
            candidate_failure_refs=candidate_failure_refs,
        )
        system = (
            "Assess the real effect of a combined Operation Smoke candidate. "
            "For every supplied initial failure ref, return resolved, "
            "persisting, or unknown. Compare only baseline and candidate HTTP "
            "evidence. Do not evaluate Generator or Constraint syntax and do "
            "not infer success from local samples. The same HTTP status code alone "
            "does not prove that the same failure persists. Compare response "
            "bodies, error fields, named parameters, and error causes. If the "
            "original parameter error disappears but a different parameter error "
            "appears, classify the original failure as resolved. Classify it as "
            "persisting when the same parameter and cause remain, and as unknown "
            "when a missing or heavily truncated body prevents a safe comparison."
            "\n\n"
            + protocol.text
        )
        effect_payload = build_effect_validation_payload(
            baseline_report=baseline_report,
            candidate_report=candidate_report,
            baseline_private_case_evidence=(
                baseline_private_case_evidence
            ),
            candidate_private_case_evidence=(
                candidate_private_case_evidence
            ),
            baseline_failures=baseline_failures,
            candidate_failures=candidate_failures,
            confirmed_diagnoses=actionables,
            group_failure_mapping=[
                {
                    "group_id": group.group_id,
                    "root_failure_refs": group.root_failure_refs,
                }
                for group in groups
            ],
            redactor=self.tracing_runtime.redactor,
        )
        user = json.dumps(
            effect_payload,
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        )
        messages = [
            LLMMessage(role="system", content=system),
            LLMMessage(role="user", content=user),
        ]
        response = self.client.invoke(
            self._request(
                model=self.effect_model,
                messages=messages,
                role="operation_smoke_effect_validation",
                tools=[],
                tool_choice="none",
            )
        )
        decision, errors = self._parse(response, PatchValidationDecision)
        if decision is not None and not errors:
            errors = _effect_decision_errors(
                decision,
                target_refs=target_refs,
                candidate_failure_refs=candidate_failure_refs,
            )
        if errors:
            response = self._repair(
                model=self.effect_model,
                messages=messages,
                response=response,
                role="operation_smoke_effect_validation",
                errors=errors,
                guidance=system,
            )
            decision, errors = self._parse(
                response,
                PatchValidationDecision,
            )
            if decision is not None and not errors:
                errors = _effect_decision_errors(
                    decision,
                    target_refs=target_refs,
                    candidate_failure_refs=candidate_failure_refs,
                )
        if decision is None or errors:
            items = [
                PatchItemValidationSummary(
                    item_id=reference,
                    status="unknown",
                    reason="Effect validation output was invalid.",
                    confidence=0,
                )
                for reference in target_refs
            ]
        else:
            by_ref = {item.item_id: item for item in decision.items}
            items = [
                PatchItemValidationSummary.model_validate(
                    by_ref[reference].model_dump(mode="json")
                )
                for reference in target_refs
            ]
        resolved_refs = {
            item.item_id for item in items if item.status == "resolved"
        }
        accepted_groups = [
            group
            for group in groups
            if resolved_refs.intersection(group.root_failure_refs)
        ]
        rejected_groups = [
            group for group in groups if group not in accepted_groups
        ]
        return PatchValidationSummary(
            items=items,
            accepted_item_ids=[
                item.item_id for item in items if item.status == "resolved"
            ],
            accepted_group_ids=[
                group.group_id for group in accepted_groups
            ],
            rejected_group_ids=[
                group.group_id for group in rejected_groups
            ],
            accepted_input_node_ids=list(
                dict.fromkeys(
                    update.input_node_id
                    for group in accepted_groups
                    for update in group.patch.updates
                )
            ),
            rejected_input_node_ids=list(
                dict.fromkeys(
                    update.input_node_id
                    for group in rejected_groups
                    for update in group.patch.updates
                )
            ),
            accepted_constraint_ids=list(
                dict.fromkeys(
                    constraint.constraint_id
                    for group in accepted_groups
                    for constraint in group.patch.constraints
                )
            ),
            rejected_constraint_ids=list(
                dict.fromkeys(
                    constraint.constraint_id
                    for group in rejected_groups
                    for constraint in group.patch.constraints
                )
            ),
        )

    def _diagnose_failures(
        self,
        *,
        report: OperationExecutionReport,
        config: OperationGeneratorConfig,
        private_case_evidence: Mapping[str, Any] | None,
        max_outputs_per_failure: int,
    ) -> PlanSolveDiagnosisResult:
        """Run the bounded FIFO of initial and probe-discovered failures."""
        if not self.planning_model.enabled:
            raise OperationSmokeOutputError(
                "operation_smoke_planning_model_not_configured",
                "The Operation Smoke planning model is not configured",
            )
        if report.operation_key != config.operation_key:
            raise OperationSmokeOutputError(
                "operation_smoke_report_mismatch",
                "Execution report and generator config identify different operations",
            )
        # The journal assigns short stable aliases (F/C/O) and retains richer
        # private evidence only in memory. Models cite aliases, not arbitrary
        # copies of case or response text.
        journal = EvidenceJournal.from_batch(
            report=report,
            config=config,
            private_case_evidence=private_case_evidence,
            redactor=self.tracing_runtime.redactor,
        )
        initial_refs = list(journal.failure_aliases)
        initial_ref_set = set(initial_refs)
        queue = initial_refs[:MAX_DIAGNOSIS_FAILURES]
        roots_by_ref: dict[str, list[str]] = {
            failure_ref: [failure_ref] for failure_ref in initial_refs
        }
        truncated = initial_refs[MAX_DIAGNOSIS_FAILURES:]
        investigations: list[FailureInvestigationSummary] = []
        actionable: list[ActionableFailure] = []
        deferred: list[DeferredFailure] = []

        # The queue may grow while consumed: probes can reveal another unique
        # failure, appended only while the shared ten-item capacity remains.
        position = 0
        while position < len(queue):
            failure_ref = queue[position]
            roots = roots_by_ref[failure_ref]
            before_observations = set(journal.observation_aliases)
            with self.tracing_runtime.span(
                "OperationSmokeDiagnoser.investigate_failure",
                kind="AGENT",
                input_value={
                    "operation_key": config.operation_key,
                    "failure_ref": failure_ref,
                    "root_failure_refs": roots,
                    "queue_position": position,
                },
                attributes={
                    "restscope.operation.key": config.operation_key,
                    "restscope.smoke.failure_ref": failure_ref,
                    "restscope.smoke.failure_queue_position": position,
                },
            ) as failure_span:
                outcome, summary = self._investigate_one_failure(
                    journal=journal,
                    config=config,
                    failure_ref=failure_ref,
                    root_failure_refs=roots,
                    item_id=f"I{position + 1}",
                    is_initial=failure_ref in initial_ref_set,
                    max_outputs=max_outputs_per_failure,
                )
                failure_span.set_output(summary.model_dump(mode="json"))
                failure_span.set_attribute(
                    "restscope.smoke.failure_status",
                    summary.status,
                )
                failure_span.set_attribute(
                    "restscope.smoke.failure_valid_outputs",
                    summary.valid_outputs,
                )
                failure_span.set_attribute(
                    "restscope.smoke.failure_http_tool_calls",
                    summary.http_tool_calls,
                )
            investigations.append(summary)
            if isinstance(outcome, ActionableFailure):
                actionable.append(outcome)
            else:
                deferred.append(outcome)

            # Only observations added by this investigation can introduce new
            # queue items. Their provenance inherits all original root failures.
            new_observations = [
                reference
                for reference in journal.observation_aliases
                if reference not in before_observations
            ]
            discovered_refs = list(
                dict.fromkeys(
                    discovered
                    for observation_ref in new_observations
                    for discovered in journal.observation_failure_refs.get(
                        observation_ref,
                        [],
                    )
                )
            )
            for discovered_ref in discovered_refs:
                inherited = roots_by_ref.setdefault(discovered_ref, [])
                for root_ref in roots:
                    if root_ref not in inherited:
                        inherited.append(root_ref)
                _refresh_provenance(
                    failure_ref=discovered_ref,
                    roots=inherited,
                    investigations=investigations,
                    actionable=actionable,
                    deferred=deferred,
                )
                if discovered_ref in queue or discovered_ref in truncated:
                    continue
                if len(queue) < MAX_DIAGNOSIS_FAILURES:
                    queue.append(discovered_ref)
                else:
                    truncated.append(discovered_ref)
            position += 1

        status = (
            "actionable"
            if actionable
            else "no_parameter_issue"
            if investigations
            and all(item.reason == "non_parameter" for item in deferred)
            else "inconclusive"
        )
        return PlanSolveDiagnosisResult(
            status=status,
            termination_reason=(
                "all_failures_processed" if queue else "no_failures"
            ),
            investigations=investigations,
            actionable_failures=actionable,
            deferred_failures=deferred,
            truncated_failure_refs=truncated,
            valid_outputs=sum(item.valid_outputs for item in investigations),
            invalid_outputs=sum(
                item.invalid_outputs for item in investigations
            ),
            http_tool_calls=sum(
                item.http_tool_calls for item in investigations
            ),
        )

    def _investigate_one_failure(
        self,
        *,
        journal: EvidenceJournal,
        config: OperationGeneratorConfig,
        failure_ref: str,
        root_failure_refs: list[str],
        item_id: str,
        is_initial: bool,
        max_outputs: int,
    ) -> tuple[
        ActionableFailure | DeferredFailure,
        FailureInvestigationSummary,
    ]:
        """Run the decision/probe loop for exactly one active failure."""
        state = FailureInvestigationState(
            failure_ref=failure_ref,
            root_failure_refs=root_failure_refs,
        )

        while state.valid_outputs < max_outputs:
            # HTTP capability remains hidden until a hypothesis states which
            # inputs change and what response change would confirm it.
            prompt = build_failure_investigation_prompt(
                config=config,
                journal=journal,
                failure_ref=failure_ref,
                root_failure_refs=root_failure_refs,
                active_hypothesis=state.active_hypothesis,
                inherited_observation_refs=(
                    state.inherited_observation_refs
                ),
                probe_observation_refs=state.probe_observation_refs,
            )
            tools = (
                [self.http_probe.tool_spec(config)]
                if state.active_hypothesis is not None
                and self.http_probe is not None
                else []
            )
            messages = [
                LLMMessage(role="system", content=prompt.system),
                LLMMessage(role="user", content=prompt.user),
            ]
            response = self.client.invoke(
                self._request(
                    model=self.planning_model,
                    messages=messages,
                    role="operation_smoke_root_cause_diagnosis",
                    tools=tools,
                    tool_choice="auto" if tools else "none",
                )
            )
            decision, errors = self._failure_response(
                response,
                journal=journal,
                config=config,
                failure_ref=failure_ref,
                active_hypothesis=state.active_hypothesis,
                hypothesis_observation_refs=(
                    state.inherited_observation_refs
                    | state.probe_observation_refs
                ),
                tools_allowed=bool(tools),
            )
            stalled_hypothesis = False
            if not errors:
                progress_error, stalled_hypothesis = (
                    _hypothesis_progress_error(decision, state=state)
                )
                if progress_error is not None:
                    errors.append(progress_error)
            # Invalid schema/tool output does not spend the valid-output budget,
            # but three consecutive invalid replies defer this failure.
            while errors:
                state.consecutive_invalid_outputs += 1
                state.invalid_outputs += 1
                if stalled_hypothesis:
                    return _deferred_outcome(
                        failure_ref=failure_ref,
                        root_failure_refs=root_failure_refs,
                        reason="stalled_hypothesis",
                        valid_outputs=state.valid_outputs,
                        invalid_outputs=state.invalid_outputs,
                        hypothesis_count=state.hypothesis_count,
                        http_tool_calls=state.http_tool_calls,
                    )
                if (
                    state.consecutive_invalid_outputs
                    >= MAX_CONSECUTIVE_INVALID_OUTPUTS
                ):
                    break
                response = self._repair(
                    model=self.planning_model,
                    messages=messages,
                    response=response,
                    role="operation_smoke_root_cause_diagnosis",
                    errors=errors,
                    guidance=prompt.repair_guidance,
                    tools=tools,
                    tool_choice="auto" if tools else "none",
                )
                decision, errors = self._failure_response(
                    response,
                    journal=journal,
                    config=config,
                    failure_ref=failure_ref,
                    active_hypothesis=state.active_hypothesis,
                    hypothesis_observation_refs=(
                        state.inherited_observation_refs
                        | state.probe_observation_refs
                    ),
                    tools_allowed=bool(tools),
                )
                stalled_hypothesis = False
                if not errors:
                    progress_error, stalled_hypothesis = (
                        _hypothesis_progress_error(decision, state=state)
                    )
                    if progress_error is not None:
                        errors.append(progress_error)
            if errors:
                return _deferred_outcome(
                    failure_ref=failure_ref,
                    root_failure_refs=root_failure_refs,
                    reason="invalid_output_limit",
                    valid_outputs=state.valid_outputs,
                    invalid_outputs=state.invalid_outputs,
                    hypothesis_count=state.hypothesis_count,
                    http_tool_calls=state.http_tool_calls,
                )

            state.consecutive_invalid_outputs = 0
            state.valid_outputs += 1
            if response.tool_calls:
                # Every call was atomically prevalidated by `_failure_response`;
                # each result now becomes an Observation owned by this hypothesis.
                assert self.http_probe is not None
                for tool_call in response.tool_calls:
                    result = self.http_probe.execute(
                        config=config,
                        tool_call=tool_call,
                    )
                    observation_ref, _ = journal.record_tool_result(
                        tool_call,
                        result,
                    )
                    state.probe_observation_refs.add(observation_ref)
                state.http_tool_calls += len(response.tool_calls)
                continue

            assert decision is not None
            if decision.action in {"ready", "confirmed"}:
                # `ready` uses existing evidence; `confirmed` uses observations
                # owned by the active hypothesis. Both produce the same handoff.
                assert decision.cause is not None
                affected_inputs = list(
                    dict.fromkeys(
                        item.input for item in decision.solutions
                    )
                )
                actionable = ActionableFailure(
                    item_id=item_id,
                    failure_ref=failure_ref,
                    root_failure_refs=root_failure_refs,
                    evidence_origin=(
                        "initial"
                        if is_initial and decision.action == "ready"
                        else "probe"
                    ),
                    cause=decision.cause,
                    solutions=decision.solutions,
                    affected_inputs=affected_inputs,
                    evidence_refs=decision.evidence_refs,
                    interaction_notes=decision.interaction_notes,
                )
                return actionable, FailureInvestigationSummary(
                    failure_ref=failure_ref,
                    root_failure_refs=root_failure_refs,
                    status=decision.action,
                    valid_outputs=state.valid_outputs,
                    invalid_outputs=state.invalid_outputs,
                    hypothesis_count=state.hypothesis_count,
                    http_tool_calls=state.http_tool_calls,
                )
            if decision.action == "hypothesis":
                # A replacement hypothesis inherits only observations it cites.
                # New probes begin a fresh ownership set.
                state.hypothesis_count += 1
                assert decision.hypothesis is not None
                assert decision.expected_outcome is not None
                state.active_hypothesis = FailureHypothesis(
                    hypothesis_id=f"H{state.hypothesis_count}",
                    statement=decision.hypothesis,
                    target_inputs=decision.target_inputs,
                    proposed_changes=decision.proposed_changes,
                    expected_outcome=decision.expected_outcome,
                    evidence_refs=decision.evidence_refs,
                )
                state.inherited_observation_refs = {
                    reference
                    for reference in decision.evidence_refs
                    if reference in journal.observation_aliases
                }
                state.probe_observation_refs = set()
                continue
            return _deferred_outcome(
                failure_ref=failure_ref,
                root_failure_refs=root_failure_refs,
                reason=decision.reason or "deferred",
                valid_outputs=state.valid_outputs,
                invalid_outputs=state.invalid_outputs,
                hypothesis_count=state.hypothesis_count,
                http_tool_calls=state.http_tool_calls,
            )

        return _deferred_outcome(
            failure_ref=failure_ref,
            root_failure_refs=root_failure_refs,
            reason="output_limit",
            valid_outputs=state.valid_outputs,
            invalid_outputs=state.invalid_outputs,
            hypothesis_count=state.hypothesis_count,
            http_tool_calls=state.http_tool_calls,
        )

    def _failure_response(
        self,
        response: LLMResponse,
        *,
        journal: EvidenceJournal,
        config: OperationGeneratorConfig,
        failure_ref: str,
        active_hypothesis: FailureHypothesis | None,
        hypothesis_observation_refs: set[str],
        tools_allowed: bool,
    ) -> tuple[FailureDecision | None, list[str]]:
        """
        Handle failure response as part of the run-local Operation Smoke diagnosis and
        candidate workflow.

        This private helper keeps one transformation or policy decision explicit so the
        surrounding orchestration remains readable.
        """
        tool_errors = _tool_response_errors(
            response,
            tools_allowed=tools_allowed,
        )
        if response.tool_calls:
            if not tool_errors and self.http_probe is not None:
                tool_errors.extend(
                    error
                    for call in response.tool_calls
                    if (
                        error := self.http_probe.validate(
                            config=config,
                            tool_call=call,
                        )
                    )
                )
            return None, tool_errors

        decision, errors = self._parse(response, FailureDecision)
        if decision is None:
            return None, errors
        errors.extend(decision.semantic_errors())
        known_inputs = set(journal.semantic_inputs.node_by_handle)
        known_evidence = journal.known_evidence_refs
        for handle in [
            *decision.target_inputs,
            *(item.input for item in decision.solutions),
        ]:
            if handle not in known_inputs:
                errors.append(f"{handle} was not offered as an input.")
        for evidence_ref in decision.evidence_refs:
            if evidence_ref not in known_evidence:
                errors.append(
                    f"{evidence_ref} was not supplied as evidence."
                )
        if decision.action == "ready" and active_hypothesis is not None:
            errors.append(
                "An active hypothesis must be confirmed, replaced, or deferred."
            )
        if decision.action == "confirmed":
            if active_hypothesis is None:
                errors.append("confirmed requires an active hypothesis.")
            cited_observations = {
                reference
                for reference in decision.evidence_refs
                if reference.startswith("O")
            }
            if not cited_observations:
                errors.append(
                    "confirmed requires an HTTP observation reference."
                )
            elif not cited_observations.issubset(
                hypothesis_observation_refs
            ):
                errors.append(
                    "confirmed may only cite observations from the active "
                    "hypothesis."
                )
        return decision, errors

    def _request(
        self,
        *,
        model: LLMModelConfig,
        messages: list[LLMMessage],
        role: str,
        tools: list[ToolSpec],
        tool_choice: str,
    ) -> LLMRequest:
        return LLMRequest(
            provider=model.provider,
            model=model.model,
            messages=messages,
            temperature=0,
            max_tokens=model.max_tokens,
            response_format="json",
            tools=tools,
            tool_choice=tool_choice,
            timeout_seconds=model.timeout_seconds,
            reasoning=model.reasoning,
            metadata={"role": role},
        )

    def _parse(
        self,
        response: LLMResponse,
        output_model: type[_OutputT],
    ) -> tuple[_OutputT | None, list[str]]:
        validation = self.validator.validate(
            response=response,
            output_model=output_model,
        )
        if not validation.valid:
            return None, [
                (
                    f"{issue.location}: {issue.message}"
                    if issue.location
                    else issue.message
                )
                for issue in validation.errors[:_MAX_REPAIR_ERRORS]
            ]
        return output_model.model_validate(validation.validated_object), []

    def _repair(
        self,
        *,
        model: LLMModelConfig,
        messages: list[LLMMessage],
        response: LLMResponse,
        role: str,
        errors: list[str],
        guidance: str | None = None,
        tools: list[ToolSpec] | None = None,
        tool_choice: str = "none",
    ) -> LLMResponse:
        """
        Handle repair as part of the run-local Operation Smoke diagnosis and candidate
        workflow.

        This private helper keeps one transformation or policy decision explicit so the
        surrounding orchestration remains readable.
        """
        return self.client.invoke(
            self._request(
                model=model,
                messages=[
                    *messages,
                    LLMMessage(
                        role="assistant",
                        content=_response_json(response),
                    ),
                    LLMMessage(
                        role="user",
                        content=(
                            "Your previous output could not be used.\n"
                            + "\n".join(
                                f"- {error}"
                                for error in errors[:_MAX_REPAIR_ERRORS]
                            )
                            + (
                                "\n" + guidance
                                if guidance is not None
                                else ""
                            )
                            + "\nReturn one complete corrected JSON object "
                            "using only the supplied names and references."
                        ),
                    ),
                ],
                role=role,
                tools=list(tools or []),
                tool_choice=tool_choice,
            )
        )


def _hypothesis_progress_error(
    decision: FailureDecision | None,
    *,
    state: FailureInvestigationState,
) -> tuple[str | None, bool]:
    """
    Handle hypothesis progress error as part of the run-local Operation Smoke diagnosis
    and candidate workflow.

    This private helper keeps one transformation or policy decision explicit so the
    surrounding orchestration remains readable.
    """
    if decision is None or decision.action != "hypothesis":
        return None, False
    assert decision.expected_outcome is not None
    signature = json.dumps(
        {
            "target_inputs": list(dict.fromkeys(decision.target_inputs)),
            "proposed_changes": [
                _normalized_hypothesis_text(value)
                for value in decision.proposed_changes
            ],
            "expected_outcome": _normalized_hypothesis_text(
                decision.expected_outcome
            ),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    if signature == state.last_hypothesis_signature:
        state.repeated_hypothesis_outputs = min(
            3,
            state.repeated_hypothesis_outputs + 1,
        )
    else:
        state.last_hypothesis_signature = signature
        state.repeated_hypothesis_outputs = 1
    if state.repeated_hypothesis_outputs < 2:
        return None, False
    return (
        "The hypothesis repeats the same target inputs, proposed changes, "
        "and expected outcome. Probe it, confirm it, materially replace it, "
        "or defer it.",
        state.repeated_hypothesis_outputs >= 3,
    )


def _normalized_hypothesis_text(value: str) -> str:
    return " ".join(value.split()).casefold()


def _deferred_outcome(
    *,
    failure_ref: str,
    root_failure_refs: list[str],
    reason: str,
    valid_outputs: int,
    invalid_outputs: int,
    hypothesis_count: int,
    http_tool_calls: int,
) -> tuple[DeferredFailure, FailureInvestigationSummary]:
    """
    Handle deferred outcome as part of the run-local Operation Smoke diagnosis and
    candidate workflow.

    This private helper keeps one transformation or policy decision explicit so the
    surrounding orchestration remains readable.
    """
    return (
        DeferredFailure(
            failure_ref=failure_ref,
            root_failure_refs=root_failure_refs,
            reason=reason,
        ),
        FailureInvestigationSummary(
            failure_ref=failure_ref,
            root_failure_refs=root_failure_refs,
            status="deferred",
            valid_outputs=valid_outputs,
            invalid_outputs=invalid_outputs,
            hypothesis_count=hypothesis_count,
            http_tool_calls=http_tool_calls,
            reason=reason,
        ),
    )


def _refresh_provenance(
    *,
    failure_ref: str,
    roots: list[str],
    investigations: list[FailureInvestigationSummary],
    actionable: list[ActionableFailure],
    deferred: list[DeferredFailure],
) -> None:
    """Merge newly discovered roots into an already processed failure."""

    for collection in (investigations, actionable, deferred):
        for index, item in enumerate(collection):
            if item.failure_ref == failure_ref:
                collection[index] = item.model_copy(
                    update={"root_failure_refs": list(roots)}
                )


def _tool_response_errors(
    response: LLMResponse,
    *,
    tools_allowed: bool,
) -> list[str]:
    if not response.tool_calls:
        return []
    errors: list[str] = []
    if not tools_allowed:
        errors.append("HTTP tools are not available for this decision.")
    if response.parsed_json is not None or (
        response.content is not None and response.content.strip()
    ):
        errors.append("Do not mix HTTP tool calls with a diagnosis decision.")
    if any(
        call.name != "restscope.http.request"
        for call in response.tool_calls
    ):
        errors.append("Only restscope.http.request may be called.")
    return errors


def _effect_decision_errors(
    decision: PatchValidationDecision,
    *,
    target_refs: list[str],
    candidate_failure_refs: list[str],
) -> list[str]:
    supplied = [item.item_id for item in decision.items]
    errors: list[str] = []
    for reference in target_refs:
        if supplied.count(reference) != 1:
            errors.append(f"{reference} must be classified exactly once.")
    for reference in supplied:
        if reference not in target_refs:
            errors.append(
                f"{reference} was not supplied as an initial failure."
            )
    allowed_candidate_refs = set(candidate_failure_refs)
    for item in decision.items:
        for reference in item.current_failure_refs:
            if reference not in allowed_candidate_refs:
                errors.append(
                    f"{reference} was not supplied as a candidate failure."
                )
    return errors


def _response_json(response: LLMResponse) -> str:
    value = (
        response.parsed_json
        if response.parsed_json is not None
        else response.content
        if response.content is not None
        else {
            "tool_calls": [
                {
                    "name": call.name,
                    "arguments": call.arguments,
                }
                for call in response.tool_calls
            ]
        }
    )
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    )
