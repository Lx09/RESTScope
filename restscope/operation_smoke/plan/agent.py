"""Classify one Batch using a small set of retrieved historical Failures.

The LLM owns the semantic decision: whether observations describe the same
Failure, whether an existing Failure should be reused, and whether work is
debuggable. Runtime code retrieves candidates, owns identity and durability:
it creates temporary references, rejects forged references, checks complete
observation coverage, and writes memory only after the final output is valid.
"""

from __future__ import annotations

import re
from typing import Protocol

from restscope.context import AgentContext, CompactTextWriter, ContextLimits
from restscope.llm import (
    LLMClient,
    LLMModelConfig,
    LLMRequest,
    LLMResponse,
    OutputValidator,
)
from restscope.observability import TracingRuntime
from restscope.operation_smoke.memory import (
    FailureCandidate,
    FailureClassificationWrite,
    FailureObservationWrite,
    FailureRetrievalObservation,
    PlanMemoryWrite,
    RecordedPlan,
)
from .schemas import (
    FailureClassificationDecision,
    FailureTodo,
    NonDebuggableFailure,
    SmokePlanDecision,
    SmokePlanRequest,
    SmokeRoundPlan,
)

class PlannerMemory(Protocol):
    """Describe the read/write memory operations owned by Planner runtime."""

    def find_failure_candidates(
        self,
        operation_key: str,
        observations: list[FailureRetrievalObservation],
    ) -> list[FailureCandidate]:
        """Return only plausible operation-scoped Failures for current cases."""
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
        system_prompt: str | None = None,
        validator: OutputValidator | None = None,
        tracing_runtime: TracingRuntime | None = None,
    ) -> None:
        """Store the model, Memory Interface, prompt, and tracing boundary.

        ``system_prompt`` replaces the complete instruction message only for an
        explicit evaluation. Production composition leaves it unset and keeps
        using the built-in prompt.
        """
        self.client = client
        self.model = model
        self.memory = memory
        self.system_prompt = system_prompt or _system_prompt()
        self.validator = validator or OutputValidator()
        self.tracing_runtime = tracing_runtime or TracingRuntime.disabled()

    def plan(
        self,
        request: SmokePlanRequest,
        *,
        max_outputs: int = 50,
    ) -> SmokeRoundPlan:
        """Classify all failed observations and persist only a valid final Plan.

        Runtime retrieves a bounded candidate set before the first output, so a
        normal classification needs one model response and Planner exposes no
        memory tool. Persistence occurs exactly once, after DTO and semantic
        validation have both succeeded.
        """
        if not 1 <= max_outputs <= 50:
            raise ValueError("max_outputs must be between 1 and 50")
        if not self.model.enabled:
            raise RuntimeError("The Operation Smoke Plan model is not configured")

        retrieval_observations = [
            _retrieval_observation(code, request.coded_cases[code])
            for code in request.failed_case_codes
        ]
        candidates = self.memory.find_failure_candidates(
            request.operation_key,
            retrieval_observations,
        )
        candidates = candidates[:24]
        failure_id_by_ref = {
            f"F{index}": candidate.failure_id
            for index, candidate in enumerate(candidates, start=1)
        }
        rendered = _planner_context_text(
            request=request,
            candidates=candidates,
        )
        context = AgentContext(
            system=self.system_prompt,
            user=rendered.text,
            limits=ContextLimits(
                system_chars=2_500,
                initial_user_chars=18_000,
                feedback_chars=4_000,
                conversation_chars=24_000,
                required_output_tokens=self.model.max_tokens,
            ),
            metrics=rendered.metrics,
        )
        last_errors: list[str] = []

        with self.tracing_runtime.span(
            "SmokePlanAgent.plan",
            kind="AGENT",
            input_value={
                "operation_key": request.operation_key,
                "round_number": request.round_number,
                "failed_case_count": len(request.failed_case_codes),
                "candidate_count": len(candidates),
            },
        ) as span:
            for name, value in context.metrics.trace_attributes().items():
                span.set_attribute(name, value)
            for output_number in range(1, max_outputs + 1):
                response = self.client.invoke(
                    LLMRequest(
                        provider=self.model.provider,
                        model=self.model.model,
                        messages=context.messages_for_request(self.model),
                        temperature=0,
                        max_tokens=self.model.max_tokens,
                        response_format="json",
                        tools=[],
                        tool_choice="none",
                        timeout_seconds=self.model.timeout_seconds,
                        reasoning=self.model.reasoning,
                        metadata={"role": "operation_smoke_plan"},
                    )
                )

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
                    context.append_assistant(response)
                    context.append_feedback(
                        _correction_text(last_errors)
                    )
                    continue

                plan = self._record_and_expand(
                    request=request,
                    decision=decision,
                    failure_id_by_ref=failure_id_by_ref,
                    outputs_used=output_number,
                )
                for name, value in context.metrics.trace_attributes().items():
                    span.set_attribute(name, value)
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
            for name, value in context.metrics.trace_attributes().items():
                span.set_attribute(name, value)
            span.set_output(exhausted.model_dump(mode="json"))
            return exhausted

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
    """Explain the one-output classification task and candidate limitations."""
    return (
        "Classify every supplied failed C case. Group semantically identical "
        "observations; split independent Failures. Historical F candidates are "
        "a ranked subset, not a complete directory. Reuse F only when its "
        "meaning matches; otherwise set failure_ref to null. Lack of a candidate "
        "never makes a case non_debuggable. Cover every failed C code; one C may "
        "support multiple Failures, but one F may appear at most once. Mark "
        "non_debuggable only with a concrete reason. Do not diagnose parameters "
        "or propose patches. Planner has no tools. Return one SmokePlanDecision "
        "JSON object containing only action, classifications, and reason. Each "
        "classification contains item_id, failure_ref, summary, case_codes, "
        "disposition, and disposition_reason. Use "
        "action=no_debug only when every classification is non_debuggable."
    )


def _planner_context_text(
    *,
    request: SmokePlanRequest,
    candidates: list[FailureCandidate],
):
    """Render current failures and compact candidate cards without JSON dumps."""
    writer = CompactTextWriter(max_value_chars=800)
    writer.section("TASK")
    writer.record(
        "operation",
        operation_key=request.operation_key,
        round=request.round_number,
    )
    writer.detail("batch", _batch_statistics(request))

    writer.section("CURRENT FAILURES", untrusted=True)
    for code in request.failed_case_codes:
        case = request.coded_cases[code]
        response = _mapping(case.get("response"))
        writer.record(
            code,
            kind=case.get("failure") or case.get("error") or "failed-case",
            status=response.get("status_code"),
            media=response.get("media_type"),
            transport=response.get("error"),
        )
        request_evidence = _necessary_request_values(case)
        if request_evidence:
            writer.detail("input", request_evidence)
        response_evidence = {
            key: response[key]
            for key in ("error", "error_code", "message", "body")
            if key in response
        }
        if response_evidence:
            writer.detail("response", response_evidence)

    writer.section("HISTORICAL CANDIDATES")
    for index, candidate in enumerate(candidates, start=1):
        ref = f"F{index}"
        writer.record(
            ref,
            matches=candidate.matched_case_codes,
            summary=candidate.summary,
            reasons=candidate.match_reasons,
            observations=candidate.observation_count,
            investigations=candidate.investigation_count,
            patches=candidate.applied_patch_count,
            last_round=candidate.last_seen_round,
            required=False,
        )
        for investigation in candidate.recent_investigations:
            writer.record(
                "recent",
                round=investigation.round_number,
                outcome=investigation.outcome,
                cause=investigation.root_cause,
                solution=investigation.solution,
                parameters=[
                    parameter.input_node_id
                    for parameter in investigation.parameters
                ],
                patch_revision=(
                    investigation.applied_patch.generator_revision
                    if investigation.applied_patch is not None
                    else None
                ),
                required=False,
            )
    writer.record(
        "candidate-window",
        returned=len(candidates),
        maximum=24,
        truncated=len(candidates) == 24,
    )
    return writer.render(max_chars=18_000)


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


def _retrieval_observation(
    code: str,
    case: dict,
) -> FailureRetrievalObservation:
    """Project one failed Batch case into deterministic retrieval signals."""
    response = _mapping(case.get("response"))
    failure_kind = str(
        case.get("failure")
        or case.get("error")
        or response.get("error")
        or "failed case"
    )
    transport_error = response.get("error")
    if transport_error is None and case.get("response") is None:
        transport_error = case.get("error")
    error_text = " ".join(
        str(response[key])
        for key in ("error_code", "message", "error", "body")
        if response.get(key) not in (None, "")
    )
    keywords = sorted(_discriminative_terms(f"{failure_kind} {error_text}"))
    return FailureRetrievalObservation(
        case_code=code,
        failure_kind=failure_kind,
        transport_error=(
            str(transport_error) if transport_error is not None else None
        ),
        status_code=_status_code(response.get("status_code")),
        media_type=(
            str(response["media_type"])
            if response.get("media_type") is not None
            else None
        ),
        input_paths=sorted(_leaf_paths(_necessary_request_values(case))),
        error_signature=error_text[:800] or None,
        keywords=keywords,
    )


def _batch_statistics(request: SmokePlanRequest) -> dict:
    """Select only bounded aggregate fields from the Coordinator's Batch DTO."""
    allowed = (
        "run_id",
        "success_rate",
        "successful_cases",
        "failed_cases",
        "status_code_counts",
    )
    stats = {
        key: request.batch[key]
        for key in allowed
        if key in request.batch
    }
    stats["failed_case_count"] = len(request.failed_case_codes)
    stats["total_case_count"] = len(request.coded_cases)
    return stats


def _necessary_request_values(case: dict) -> dict:
    """Keep only request inputs that can explain or reproduce the failure."""
    request = _mapping(case.get("request"))
    return {
        key: request[key]
        for key in ("path_parameters", "query", "headers", "body")
        if key in request
    }


def _mapping(value: object) -> dict:
    """Return dictionary evidence or an empty mapping for absent transport data."""
    return value if isinstance(value, dict) else {}


def _leaf_paths(value: object, prefix: str = "") -> set[str]:
    """Return dotted semantic input paths without serializing their values."""
    if isinstance(value, dict):
        paths: set[str] = set()
        for key, child in value.items():
            root_key = {
                "path_parameters": "path",
                "query_parameters": "query",
                "query": "query",
                "headers": "header",
                "body": "body",
            }.get(str(key), str(key))
            next_prefix = f"{prefix}.{root_key}" if prefix else root_key
            paths.update(_leaf_paths(child, next_prefix))
        return paths
    if isinstance(value, list):
        return {prefix} if prefix else set()
    return {prefix} if prefix else set()


def _discriminative_terms(value: str) -> set[str]:
    """Extract useful error words while rejecting generic HTTP vocabulary."""
    generic = {
        "error",
        "failed",
        "failure",
        "unexpected",
        "status",
        "response",
        "request",
        "invalid",
        "application",
        "json",
    }
    return {
        term
        for term in re.findall(r"[a-z0-9_./-]{3,}", value.casefold())
        if term not in generic and not term.isdigit()
    }


def _status_code(value: object) -> int | None:
    """Normalize an HTTP status while excluding booleans."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _correction_text(errors: list[str]) -> str:
    """Render deterministic validation feedback as compact text."""
    return (
        "CORRECT COMPLETE PLAN\n"
        + "\n".join(f"issue | {error}" for error in errors)
        + "\nReturn one complete SmokePlanDecision JSON object. Planner has no tools."
    )
