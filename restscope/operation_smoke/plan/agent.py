"""Classify one Batch using stable Failure memory and optional history lookup.

The LLM owns the semantic decision: whether observations describe the same
Failure, whether an existing Failure should be reused, and whether work is
debuggable.  Runtime code owns identity and durability: it creates temporary
references, rejects forged references, checks complete observation coverage,
and writes memory only after the final output is valid.
"""

from __future__ import annotations

import json
from typing import Protocol

from restscope.llm import (
    LLMClient,
    LLMMessage,
    LLMModelConfig,
    LLMRequest,
    LLMResponse,
    OutputValidator,
    ToolCall,
    ToolSpec,
)
from restscope.observability import TracingRuntime
from restscope.operation_smoke.memory import (
    FailureCatalogEntry,
    FailureClassificationWrite,
    FailureHistory,
    FailureObservationWrite,
    PlanMemoryWrite,
    RecordedPlan,
)
from restscope.operation_smoke.prompt_context import (
    fit_message_context,
    fit_prompt_context,
)

from .schemas import (
    FailureCatalogPromptEntry,
    FailureClassificationDecision,
    FailureTodo,
    NonDebuggableFailure,
    SmokePlanDecision,
    SmokePlanRequest,
    SmokeRoundPlan,
)


_LOOKUP_TOOL_NAME = "lookup_failure_history"


class PlannerMemory(Protocol):
    """Describe the read/write memory operations owned by Planner runtime."""

    def list_operation_failures(
        self,
        operation_key: str,
    ) -> list[FailureCatalogEntry]:
        """Return the compact historical catalog included in the first prompt."""
        ...

    def lookup_failure_history(
        self,
        operation_key: str,
        failure_ids: list[str],
    ) -> list[FailureHistory]:
        """Resolve validated durable Failure IDs for the read-only memory tool."""
        ...

    def record_plan(self, write: PlanMemoryWrite) -> RecordedPlan:
        """Persist a semantically valid final Plan outside the model tool loop."""
        ...


class SmokePlanAgent:
    """Let an LLM classify failures while code protects memory identities."""

    def __init__(
        self,
        *,
        client: LLMClient,
        model: LLMModelConfig,
        memory: PlannerMemory,
        validator: OutputValidator | None = None,
        tracing_runtime: TracingRuntime | None = None,
    ) -> None:
        """Store the model, Memory Interface, validator, and tracing boundary."""
        self.client = client
        self.model = model
        self.memory = memory
        self.validator = validator or OutputValidator()
        self.tracing_runtime = tracing_runtime or TracingRuntime.disabled()

    def plan(
        self,
        request: SmokePlanRequest,
        *,
        max_outputs: int = 50,
    ) -> SmokeRoundPlan:
        """Classify all failed observations and persist only a valid final Plan.

        Every LLM response—including a response that asks for memory—consumes
        one output.  The memory tool itself is read-only.  Persistence occurs
        exactly once, after DTO and semantic validation have both succeeded.
        """
        if not 1 <= max_outputs <= 50:
            raise ValueError("max_outputs must be between 1 and 50")
        if not self.model.enabled:
            raise RuntimeError("The Operation Smoke Plan model is not configured")

        catalog = self.memory.list_operation_failures(request.operation_key)
        catalog_prompt, failure_id_by_ref = _catalog_aliases(catalog)
        fitted = fit_prompt_context(
            required={
                **request.model_dump(mode="json"),
                "failure_catalog": [
                    item.model_dump(mode="json") for item in catalog_prompt
                ],
            },
            history=[],
            model=self.model,
        )
        messages = [
            LLMMessage(role="system", content=_system_prompt()),
            LLMMessage(
                role="user",
                content=json.dumps(
                    fitted.payload,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    default=str,
                ),
            ),
        ]
        last_errors: list[str] = []

        with self.tracing_runtime.span(
            "SmokePlanAgent.plan",
            kind="AGENT",
            input_value={
                "operation_key": request.operation_key,
                "round_number": request.round_number,
                "failed_case_count": len(request.failed_case_codes),
                "catalog_size": len(catalog),
            },
        ) as span:
            for output_number in range(1, max_outputs + 1):
                response = self.client.invoke(
                    LLMRequest(
                        provider=self.model.provider,
                        model=self.model.model,
                        messages=fit_message_context(
                            messages,
                            model=self.model,
                        ).messages,
                        temperature=0,
                        max_tokens=self.model.max_tokens,
                        response_format="json",
                        tools=[_memory_tool_spec()],
                        tool_choice="auto",
                        timeout_seconds=self.model.timeout_seconds,
                        reasoning=self.model.reasoning,
                        metadata={"role": "operation_smoke_plan"},
                    )
                )

                if response.tool_calls:
                    errors = _memory_tool_errors(
                        response,
                        valid_refs=set(failure_id_by_ref),
                    )
                    if errors:
                        _append_correction(messages, response, errors)
                        last_errors = errors
                        continue
                    messages.append(
                        LLMMessage(
                            role="assistant",
                            content="",
                            tool_calls=response.tool_calls,
                        )
                    )
                    for call in response.tool_calls:
                        refs = list(call.arguments["failure_refs"])
                        histories = self._lookup_histories(
                            operation_key=request.operation_key,
                            refs=refs,
                            failure_id_by_ref=failure_id_by_ref,
                        )
                        messages.append(
                            LLMMessage(
                                role="tool",
                                name=_LOOKUP_TOOL_NAME,
                                tool_call_id=call.id,
                                content=json.dumps(
                                    {
                                        "failures": [
                                            _history_for_prompt(
                                                ref=ref,
                                                history=history,
                                            )
                                            for ref, history in zip(
                                                refs,
                                                histories,
                                                strict=True,
                                            )
                                        ]
                                    },
                                    ensure_ascii=False,
                                    separators=(",", ":"),
                                    default=str,
                                ),
                            )
                        )
                    continue

                decision, errors = _parse_decision(
                    response,
                    validator=self.validator,
                )
                if decision is not None:
                    errors.extend(
                        _semantic_errors(
                            decision,
                            request=request,
                            valid_failure_refs=set(failure_id_by_ref),
                        )
                    )
                if errors or decision is None:
                    last_errors = errors or ["The Plan output could not be used."]
                    _append_correction(messages, response, last_errors)
                    continue

                plan = self._record_and_expand(
                    request=request,
                    decision=decision,
                    failure_id_by_ref=failure_id_by_ref,
                    outputs_used=output_number,
                )
                span.set_output(
                    {
                        "status": plan.status,
                        "todo_count": len(plan.todos),
                        "non_debuggable_count": len(plan.non_debuggable),
                        "outputs_used": plan.outputs_used,
                    }
                )
                return plan

            exhausted = SmokeRoundPlan(
                status="plan_budget_exhausted",
                reason="; ".join(
                    last_errors or ["The Plan output budget was exhausted."]
                ),
                outputs_used=max_outputs,
            )
            span.set_output(exhausted.model_dump(mode="json"))
            return exhausted

    def _lookup_histories(
        self,
        *,
        operation_key: str,
        refs: list[str],
        failure_id_by_ref: dict[str, str],
    ) -> list[FailureHistory]:
        """Resolve validated temporary aliases and trace the read-only lookup."""
        with self.tracing_runtime.span(
            "SmokePlanAgent.lookup_failure_history",
            kind="TOOL",
            input_value={"failure_refs": refs},
        ) as span:
            histories = self.memory.lookup_failure_history(
                operation_key,
                [failure_id_by_ref[ref] for ref in refs],
            )
            span.set_output({"failure_count": len(histories)})
            return histories

    def _record_and_expand(
        self,
        *,
        request: SmokePlanRequest,
        decision: SmokePlanDecision,
        failure_id_by_ref: dict[str, str],
        outputs_used: int,
    ) -> SmokeRoundPlan:
        """Persist validated classifications, then build independent Solve work."""
        writes = [
            FailureClassificationWrite(
                failure_id=(
                    failure_id_by_ref[item.failure_ref]
                    if item.failure_ref is not None
                    else None
                ),
                summary=item.summary,
                observations=[
                    _observation_write(
                        code=code,
                        case=request.coded_cases[code],
                    )
                    for code in item.case_codes
                ],
                disposition=(
                    "planned"
                    if item.disposition == "debug"
                    else "non_debuggable"
                ),
                disposition_reason=item.disposition_reason,
            )
            for item in decision.classifications
        ]
        recorded = self.memory.record_plan(
            PlanMemoryWrite(
                operation_key=request.operation_key,
                round_number=request.round_number,
                batch_run_id=request.batch_run_id,
                classifications=writes,
            )
        )
        todos: list[FailureTodo] = []
        non_debuggable: list[NonDebuggableFailure] = []
        for item, stable in zip(
            decision.classifications,
            recorded.failures,
            strict=True,
        ):
            cases = [request.coded_cases[code] for code in item.case_codes]
            if item.disposition == "debug":
                todos.append(
                    FailureTodo(
                        todo_id=item.item_id,
                        failure_id=stable.failure_id,
                        failure=item.summary,
                        cases=cases,
                    )
                )
            else:
                assert item.disposition_reason is not None
                non_debuggable.append(
                    NonDebuggableFailure(
                        failure_id=stable.failure_id,
                        failure=item.summary,
                        reason=item.disposition_reason,
                        cases=cases,
                    )
                )
        return SmokeRoundPlan(
            status=(
                "no_debug"
                if decision.action == "no_debug"
                else "planned"
            ),
            todos=todos,
            non_debuggable=non_debuggable,
            reason=decision.reason,
            outputs_used=outputs_used,
        )


def _system_prompt() -> str:
    """Explain Planner's semantic role and the temporary-reference boundary."""
    return (
        "Classify every failed case in one Operation Smoke batch. Group "
        "semantically identical observations, reuse a catalog Failure through "
        "its F-number when appropriate, and never create two classifications "
        "for the same Failure. A case may support multiple Failures. Mark an "
        "observation non_debuggable only with a concrete reason. You may call "
        "lookup_failure_history with one or more supplied F-numbers. Do not "
        "diagnose parameters or propose patches. Return exactly action, "
        "classifications, and reason. Use action=no_debug only when no current "
        "observation needs a Solve session."
    )


def _catalog_aliases(
    catalog: list[FailureCatalogEntry],
) -> tuple[list[FailureCatalogPromptEntry], dict[str, str]]:
    """Create stable request-local aliases without exposing database IDs."""
    prompt: list[FailureCatalogPromptEntry] = []
    mapping: dict[str, str] = {}
    for index, item in enumerate(catalog, start=1):
        ref = f"F{index}"
        mapping[ref] = item.failure_id
        prompt.append(
            FailureCatalogPromptEntry(
                failure_ref=ref,
                summary=item.summary,
                observation_count=item.observation_count,
                investigation_count=item.investigation_count,
                applied_patch_count=item.applied_patch_count,
            )
        )
    return prompt, mapping


def _memory_tool_spec() -> ToolSpec:
    """Describe the only read-only capability available to Planner."""
    return ToolSpec(
        name=_LOOKUP_TOOL_NAME,
        description=(
            "Read complete Observation, Investigation, Parameter, conflict, "
            "and applied-Patch history for supplied Failure references."
        ),
        kind="local_function",
        input_schema={
            "type": "object",
            "properties": {
                "failure_refs": {
                    "type": "array",
                    "items": {"type": "string", "pattern": r"^F[1-9][0-9]*$"},
                    "minItems": 1,
                    "uniqueItems": True,
                }
            },
            "required": ["failure_refs"],
            "additionalProperties": False,
        },
        read_only=True,
        risk_level="low",
    )


def _memory_tool_errors(
    response: LLMResponse,
    *,
    valid_refs: set[str],
) -> list[str]:
    """Reject mixed, forged, malformed, or duplicate memory-tool requests."""
    errors: list[str] = []
    if response.parsed_json is not None or (
        response.content is not None and response.content.strip()
    ):
        errors.append("Do not mix memory tool calls with a Plan decision.")
    for call in response.tool_calls:
        if call.name != _LOOKUP_TOOL_NAME:
            errors.append(f"Unknown Planner tool: {call.name}")
            continue
        refs = call.arguments.get("failure_refs")
        if (
            not isinstance(refs, list)
            or not refs
            or any(not isinstance(ref, str) for ref in refs)
        ):
            errors.append("failure_refs must be a non-empty string array.")
            continue
        if len(refs) != len(set(refs)):
            errors.append("failure_refs must be unique.")
        for ref in refs:
            if ref not in valid_refs:
                errors.append(f"{ref} was not supplied in the Failure catalog.")
    return errors


def _parse_decision(
    response: LLMResponse,
    *,
    validator: OutputValidator,
) -> tuple[SmokePlanDecision | None, list[str]]:
    """Parse one strict non-tool Planner response."""
    result = validator.validate(response=response, output_model=SmokePlanDecision)
    if not result.valid:
        return None, [
            (
                f"{issue.location}: {issue.message}"
                if issue.location
                else issue.message
            )
            for issue in result.errors
        ]
    return SmokePlanDecision.model_validate(result.validated_object), []


def _semantic_errors(
    decision: SmokePlanDecision,
    *,
    request: SmokePlanRequest,
    valid_failure_refs: set[str],
) -> list[str]:
    """Enforce identity safety, uniqueness, and full failed-case coverage."""
    errors: list[str] = []
    item_ids = [item.item_id for item in decision.classifications]
    if len(item_ids) != len(set(item_ids)):
        errors.append("item_id values must be unique.")
    reused_refs = [
        item.failure_ref
        for item in decision.classifications
        if item.failure_ref is not None
    ]
    if len(reused_refs) != len(set(reused_refs)):
        errors.append("one existing Failure may appear only once in a Plan.")
    new_summaries = [
        item.summary.strip().casefold()
        for item in decision.classifications
        if item.failure_ref is None
    ]
    if len(new_summaries) != len(set(new_summaries)):
        errors.append("new Failure summaries must be semantically unique.")

    supplied = set(request.coded_cases)
    referenced: set[str] = set()
    for item in decision.classifications:
        if item.failure_ref is not None and item.failure_ref not in valid_failure_refs:
            errors.append(
                f"{item.failure_ref} was not supplied in the Failure catalog."
            )
        for code in item.case_codes:
            if code not in supplied:
                errors.append(f"{code} was not supplied as a case code.")
            else:
                referenced.add(code)
    for code in request.failed_case_codes:
        if code not in referenced:
            errors.append(f"{code} is a failed case and must be classified.")
    return errors


def _observation_write(
    *,
    code: str,
    case: dict,
) -> FailureObservationWrite:
    """Reduce one Batch case to the bounded evidence allowed in memory."""
    response = case.get("response")
    response_dict = response if isinstance(response, dict) else {}
    request = case.get("request")
    request_dict = request if isinstance(request, dict) else {}
    return FailureObservationWrite(
        observation_key=str(case.get("case_id") or code),
        trigger=str(case.get("failure") or case.get("error") or "failed case"),
        response_summary={
            key: response_dict[key]
            for key in ("status_code", "media_type", "error")
            if key in response_dict
        },
        # Request values are necessary to reproduce the trigger, but the full
        # transport object and response body are intentionally not persisted.
        necessary_values={
            key: request_dict[key]
            for key in ("path_parameters", "query", "headers", "body")
            if key in request_dict
        },
    )


def _history_for_prompt(*, ref: str, history: FailureHistory) -> dict:
    """Remove storage identities and label a history with its temporary alias."""
    payload = history.model_dump(mode="json")
    payload.pop("failure_id", None)
    # Investigation IDs exist only to join durable rows; Planner reasons from
    # their chronological content and never needs those database keys.
    for investigation in payload["investigations"]:
        investigation.pop("investigation_id", None)
    payload["failure_ref"] = ref
    return payload


def _append_correction(
    messages: list[LLMMessage],
    response: LLMResponse,
    errors: list[str],
) -> None:
    """Retain invalid output and precise repair instructions in conversation."""
    messages.extend(
        (
            LLMMessage(
                role="assistant",
                content=json.dumps(
                    response.parsed_json
                    if response.parsed_json is not None
                    else {
                        "content": response.content,
                        "tool_calls": [
                            call.model_dump(mode="json")
                            for call in response.tool_calls
                        ],
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                    default=str,
                ),
            ),
            LLMMessage(
                role="user",
                content=(
                    "Correct the complete Plan output:\n"
                    + "\n".join(f"- {error}" for error in errors)
                    + "\nYou may query supplied Failure references, or return "
                    "one complete classification decision."
                ),
            ),
        )
    )
