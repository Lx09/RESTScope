"""Bounded Plan & Solve diagnosis for one failing Operation Smoke batch."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel

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
    InputGeneratorPatch,
    OperationExecutionReport,
    OperationGeneratorConfig,
    ResourceIdentifierGenerator,
    ResponseValueGenerator,
)

from .evidence import EvidenceJournal
from .planning import PlanDecision, PlanState
from .prompts import (
    JointPatchDecision,
    build_joint_patch_prompt,
    build_plan_prompt,
    compile_joint_patch,
    generator_intent_repair_guidance,
    validate_joint_patch,
)
from .schemas import (
    AvailableReferenceOption,
    DeferredPlanItem,
    GeneratorPatchDraft,
    PlanSolveDiagnosisResult,
)


MAX_TOOL_CALLS_PER_ROUND = 4
_MAX_REPAIR_ERRORS = 10
_OutputT = TypeVar("_OutputT", bound=BaseModel)


class HTTPProbe(Protocol):
    def tool_spec(self, config: OperationGeneratorConfig) -> ToolSpec: ...

    def execute(
        self,
        *,
        config: OperationGeneratorConfig,
        tool_call: ToolCall,
    ) -> ToolResult: ...


class OperationSmokeOutputError(RuntimeError):
    """The configured models cannot safely run Operation Smoke diagnosis."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class OperationSmokeDiagnoser:
    """Analyze all failures before compiling one compatible generator patch."""

    def __init__(
        self,
        *,
        client: LLMClient,
        planning_model: LLMModelConfig,
        patch_model: LLMModelConfig,
        http_probe: HTTPProbe | None = None,
        validator: OutputValidator | None = None,
        tracing_runtime: TracingRuntime | None = None,
    ) -> None:
        self.client = client
        self.planning_model = planning_model
        self.patch_model = patch_model
        self.http_probe = http_probe
        self.validator = validator or OutputValidator()
        self.tracing_runtime = tracing_runtime or TracingRuntime.disabled()

    def diagnose(
        self,
        *,
        report: OperationExecutionReport,
        config: OperationGeneratorConfig,
        reference_options: list[AvailableReferenceOption] | None = None,
        reference_option_provider: (
            Callable[[set[str]], list[AvailableReferenceOption]] | None
        ) = None,
        private_case_evidence: Mapping[str, Any] | None = None,
        previous_experiment: Mapping[str, Any] | None = None,
        max_planning_outputs: int = 20,
        max_http_tool_rounds: int = 40,
    ) -> PlanSolveDiagnosisResult:
        _validate_budgets(
            max_planning_outputs=max_planning_outputs,
            max_http_tool_rounds=max_http_tool_rounds,
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
                "max_planning_outputs": max_planning_outputs,
                "max_http_tool_rounds": max_http_tool_rounds,
            },
            attributes={
                "restscope.operation.key": report.operation_key,
                "restscope.test.run_id": report.run_id,
                "restscope.smoke.max_planning_outputs": (
                    max_planning_outputs
                ),
                "restscope.smoke.max_http_tool_rounds": (
                    max_http_tool_rounds
                ),
            },
        ) as span:
            result = self._diagnose(
                report=report,
                config=config,
                reference_options=reference_options,
                reference_option_provider=reference_option_provider,
                private_case_evidence=private_case_evidence,
                previous_experiment=previous_experiment,
                max_planning_outputs=max_planning_outputs,
                max_http_tool_rounds=max_http_tool_rounds,
            )
            span.set_output(
                {
                    "status": result.status,
                    "termination_reason": result.termination_reason,
                    "planning_outputs": result.planning_outputs,
                    "http_tool_rounds": result.http_tool_rounds,
                    "ready_count": len(result.ready_items),
                    "pending_count": len(result.pending_items),
                    "covered_count": len(result.covered_item_ids),
                    "deferred_count": len(result.deferred_items),
                }
            )
            for name, value in (
                ("restscope.smoke.diagnosis_status", result.status),
                (
                    "restscope.smoke.termination_reason",
                    result.termination_reason,
                ),
                (
                    "restscope.smoke.planning_outputs",
                    result.planning_outputs,
                ),
                (
                    "restscope.smoke.http_tool_rounds",
                    result.http_tool_rounds,
                ),
                ("restscope.smoke.ready_count", len(result.ready_items)),
                ("restscope.smoke.pending_count", len(result.pending_items)),
                (
                    "restscope.smoke.non_parameter_count",
                    len(result.non_parameter_failures),
                ),
                (
                    "restscope.smoke.unplanned_count",
                    len(result.unplanned_failures),
                ),
                (
                    "restscope.smoke.covered_count",
                    len(result.covered_item_ids),
                ),
                (
                    "restscope.smoke.deferred_count",
                    len(result.deferred_items),
                ),
            ):
                span.set_attribute(name, value)
            return result

    def _diagnose(
        self,
        *,
        report: OperationExecutionReport,
        config: OperationGeneratorConfig,
        reference_options: list[AvailableReferenceOption] | None,
        reference_option_provider: (
            Callable[[set[str]], list[AvailableReferenceOption]] | None
        ),
        private_case_evidence: Mapping[str, Any] | None,
        previous_experiment: Mapping[str, Any] | None,
        max_planning_outputs: int,
        max_http_tool_rounds: int,
    ) -> PlanSolveDiagnosisResult:
        if not self.planning_model.enabled:
            raise OperationSmokeOutputError(
                "operation_smoke_planning_model_not_configured",
                "The Operation Smoke planning model is not configured",
            )
        if not self.patch_model.enabled:
            raise OperationSmokeOutputError(
                "operation_smoke_patch_model_not_configured",
                "The Operation Smoke patch model is not configured",
            )
        if report.operation_key != config.operation_key:
            raise OperationSmokeOutputError(
                "operation_smoke_report_mismatch",
                "Execution report and generator config identify different operations",
            )
        if reference_options is not None and reference_option_provider is not None:
            raise ValueError(
                "Provide reference_options or reference_option_provider, not both"
            )

        journal = EvidenceJournal.from_batch(
            report=report,
            config=config,
            private_case_evidence=private_case_evidence,
            redactor=self.tracing_runtime.redactor,
        )
        state: PlanState | None = None
        planning_outputs = 0
        http_tool_rounds = 0
        termination_reason = "analysis_complete"

        while True:
            if state is not None and (state.finish or not state.pending):
                termination_reason = (
                    "model_finalize" if state.finish else "analysis_complete"
                )
                break
            if planning_outputs >= max_planning_outputs:
                termination_reason = "decision_limit"
                break

            prompt = build_plan_prompt(
                config=config,
                journal=journal,
                plan_state=state,
                previous_experiment=previous_experiment,
            )
            can_probe = (
                state is not None
                and self.http_probe is not None
                and http_tool_rounds < max_http_tool_rounds
            )
            tools = (
                [self.http_probe.tool_spec(config)]
                if can_probe
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
                    role="operation_smoke_plan_solve",
                    tools=tools,
                    tool_choice="auto" if tools else "none",
                )
            )

            tool_errors = _tool_response_errors(
                response,
                tools_allowed=bool(tools),
            )
            if response.tool_calls and not tool_errors:
                assert self.http_probe is not None
                for tool_call in response.tool_calls:
                    result = self.http_probe.execute(
                        config=config,
                        tool_call=tool_call,
                    )
                    journal.record_tool_result(tool_call, result)
                http_tool_rounds += 1
                continue

            decision, errors = self._parse(response, PlanDecision)
            errors = [*tool_errors, *errors]
            next_state: PlanState | None = None
            if decision is not None and not errors:
                next_state, errors = PlanState.from_decision(
                    decision,
                    journal=journal,
                    previous=state,
                )
            if errors:
                repaired = self._repair(
                    model=self.planning_model,
                    messages=messages,
                    response=response,
                    role="operation_smoke_plan_solve",
                    errors=errors,
                )
                decision, repair_errors = self._parse(
                    repaired,
                    PlanDecision,
                )
                if repaired.tool_calls:
                    repair_errors = [
                        *repair_errors,
                        "A repair must return plan JSON, not tool calls.",
                    ]
                if decision is not None and not repair_errors:
                    next_state, repair_errors = PlanState.from_decision(
                        decision,
                        journal=journal,
                        previous=state,
                    )
                if repair_errors or next_state is None:
                    termination_reason = "planning_output_invalid"
                    break
            assert next_state is not None
            state = next_state
            planning_outputs += 1

        if state is None:
            return _result(
                status="inconclusive",
                termination_reason=termination_reason,
                state=None,
                planning_outputs=planning_outputs,
                http_tool_rounds=http_tool_rounds,
            )
        if not state.ready:
            no_parameter_issue = (
                not state.pending
                and not state.unplanned_failure_refs
                and set(state.non_parameter_failure_refs)
                == journal.known_failure_refs
            )
            return _result(
                status=(
                    "no_parameter_issue"
                    if no_parameter_issue
                    else "inconclusive"
                ),
                termination_reason=termination_reason,
                state=state,
                planning_outputs=planning_outputs,
                http_tool_rounds=http_tool_rounds,
            )

        affected_node_ids = {
            journal.semantic_inputs.node_by_handle[solution.input]
            for item in state.ready
            for solution in item.solutions
        }
        options = list(reference_options or [])
        if reference_option_provider is not None:
            options = reference_option_provider(affected_node_ids)
        patch_prompt = build_joint_patch_prompt(
            config=config,
            plan_state=state,
            journal=journal,
            reference_options=options,
        )
        patch_messages = [
            LLMMessage(role="system", content=patch_prompt.system),
            LLMMessage(role="user", content=patch_prompt.user),
        ]
        patch_response = self.client.invoke(
            self._request(
                model=self.patch_model,
                messages=patch_messages,
                role="operation_smoke_generator_patch",
                tools=[],
                tool_choice="none",
            )
        )
        patch_decision, patch_errors = self._parse(
            patch_response,
            JointPatchDecision,
        )
        if patch_decision is not None and not patch_errors:
            patch_errors = validate_joint_patch(
                patch_decision,
                prompt=patch_prompt,
                config=config,
            )
        if patch_errors:
            repaired = self._repair(
                model=self.patch_model,
                messages=patch_messages,
                response=patch_response,
                role="operation_smoke_generator_patch",
                errors=[
                    *patch_errors,
                    generator_intent_repair_guidance(),
                ],
            )
            patch_decision, patch_errors = self._parse(
                repaired,
                JointPatchDecision,
            )
            if patch_decision is not None and not patch_errors:
                patch_errors = validate_joint_patch(
                    patch_decision,
                    prompt=patch_prompt,
                    config=config,
                )
        if patch_decision is None or patch_errors:
            return _result(
                status="inconclusive",
                termination_reason="patch_output_invalid",
                state=state,
                planning_outputs=planning_outputs,
                http_tool_rounds=http_tool_rounds,
            )

        draft, selected_options = compile_joint_patch(
            patch_decision,
            prompt=patch_prompt,
        )
        deferred = [
            DeferredPlanItem(
                item_id=item.item_id,
                reason=item.reason,
            )
            for item in patch_decision.deferred_items
        ]
        if draft is None:
            return _result(
                status="inconclusive",
                termination_reason="all_ready_items_deferred",
                state=state,
                planning_outputs=planning_outputs,
                http_tool_rounds=http_tool_rounds,
                covered_item_ids=patch_decision.covered_item_ids,
                deferred_items=deferred,
            )
        resolved_updates = _resolve_generator_updates(
            draft,
            reference_options=selected_options,
        )
        return _result(
            status="patch_ready",
            termination_reason=termination_reason,
            state=state,
            planning_outputs=planning_outputs,
            http_tool_rounds=http_tool_rounds,
            patch=GeneratorPatchDraft(updates=resolved_updates),
            selected_reference_options=selected_options,
            covered_item_ids=patch_decision.covered_item_ids,
            deferred_items=deferred,
        )

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
            temperature=model.temperature,
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
    ) -> LLMResponse:
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
                            + "\nReturn one complete corrected JSON object "
                            "using only the supplied names and references."
                        ),
                    ),
                ],
                role=role,
                tools=[],
                tool_choice="none",
            )
        )


def _result(
    *,
    status: str,
    termination_reason: str,
    state: PlanState | None,
    planning_outputs: int,
    http_tool_rounds: int,
    patch: GeneratorPatchDraft | None = None,
    selected_reference_options: list[AvailableReferenceOption] | None = None,
    covered_item_ids: list[str] | None = None,
    deferred_items: list[DeferredPlanItem] | None = None,
) -> PlanSolveDiagnosisResult:
    return PlanSolveDiagnosisResult(
        status=status,
        termination_reason=termination_reason,
        patch=patch,
        selected_reference_options=selected_reference_options or [],
        ready_items=(
            [item.summary() for item in state.ready]
            if state is not None
            else []
        ),
        pending_items=(
            [item.summary() for item in state.pending]
            if state is not None
            else []
        ),
        non_parameter_failures=(
            state.non_parameter_failure_refs if state is not None else []
        ),
        unplanned_failures=(
            state.unplanned_failure_refs if state is not None else []
        ),
        covered_item_ids=covered_item_ids or [],
        deferred_items=deferred_items or [],
        planning_outputs=planning_outputs,
        http_tool_rounds=http_tool_rounds,
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
    if len(response.tool_calls) > MAX_TOOL_CALLS_PER_ROUND:
        errors.append(
            f"At most {MAX_TOOL_CALLS_PER_ROUND} HTTP requests are allowed "
            "in one tool round."
        )
    if response.parsed_json is not None or (
        response.content is not None and response.content.strip()
    ):
        errors.append("Do not mix HTTP tool calls with a plan decision.")
    if any(
        call.name != "restscope.http.request"
        for call in response.tool_calls
    ):
        errors.append("Only restscope.http.request may be called.")
    return errors


def _resolve_generator_updates(
    draft: GeneratorPatchDraft,
    *,
    reference_options: list[AvailableReferenceOption],
) -> list[InputGeneratorPatch]:
    options_by_id = {item.option_id: item for item in reference_options}
    updates = list(draft.updates)
    for selection in draft.reference_selections:
        option = options_by_id[selection.reference_option_id]
        if option.kind == "resource_identifier":
            assert option.canonical_resource is not None
            strategy = ResourceIdentifierGenerator(
                type="resource_identifier",
                resource=option.canonical_resource,
            )
        else:
            assert option.value_name is not None
            strategy = ResponseValueGenerator(
                type="response_value",
                value_name=option.value_name,
            )
        updates.append(
            InputGeneratorPatch(
                input_node_id=selection.input_node_id,
                inclusion_probability=selection.inclusion_probability,
                strategy=strategy,
            )
        )
    return updates


def _validate_budgets(
    *,
    max_planning_outputs: int,
    max_http_tool_rounds: int,
) -> None:
    if not 1 <= max_planning_outputs <= 20:
        raise ValueError("max_planning_outputs must be between 1 and 20")
    if not 0 <= max_http_tool_rounds <= 40:
        raise ValueError("max_http_tool_rounds must be between 0 and 40")


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
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
