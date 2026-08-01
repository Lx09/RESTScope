"""Let an LLM group distinct current-Batch messages by causal Parameters.

The deterministic :class:`FailureDeduplicator` calls this Agent only when exact
message deduplication leaves several observations. The Agent owns the semantic
grouping decision; this module owns bounded Markdown construction, strict JSON
validation, correction feedback, and output-budget accounting.
"""

from __future__ import annotations

from typing import Any

from restscope.capabilities import (
    AgentToolbox,
    OpenAPICapability,
    openapi_list_inputs_tool_spec,
)
from restscope.context import AgentContext, CompactTextWriter, ContextLimits
from restscope.llm import (
    LLMClient,
    LLMModelConfig,
    LLMRequest,
    LLMResponse,
    OutputValidator,
    ToolCall,
    ToolResult,
    ToolSpec,
)
from restscope.observability import TracingRuntime
from restscope.operation_smoke.test_case_catalog import (
    CATALOG_QUERY_TOOL_NAME,
    TestCaseCatalog,
    catalog_query_tool_spec,
    query_catalog,
    tool_result_json,
)

from .prompts import SYSTEM_PROMPT
from .schemas import FailureDedupDecision


class FailureDedupAgent:
    """Use one or more corrected LLM outputs to classify distinct messages."""

    def __init__(
        self,
        *,
        client: LLMClient,
        model: LLMModelConfig,
        openapi_capability: OpenAPICapability,
        system_prompt: str | None = None,
        validator: OutputValidator | None = None,
        tracing_runtime: TracingRuntime | None = None,
    ) -> None:
        """Store stateless collaborators used by each isolated Dedup call."""
        self.client = client
        self.model = model
        self.openapi_capability = openapi_capability
        self.system_prompt = system_prompt or SYSTEM_PROMPT
        self.validator = validator or OutputValidator()
        self.tracing_runtime = tracing_runtime or TracingRuntime.disabled()

    def deduplicate(
        self,
        *,
        operation_key: str,
        semantic_parameters: list[str],
        observations: list[dict[str, Any]],
        catalog: TestCaseCatalog,
        max_outputs: int,
    ) -> tuple[FailureDedupDecision | None, int, int, list[str]]:
        """Return a valid complete classification or report budget exhaustion.

        ``observations`` already contains one representative case per exact
        message. Every invalid response is retained as an assistant message and
        followed by bounded Markdown feedback. The tuple contains the decision,
        outputs consumed, correction count, and final validation problems.
        """
        if not 1 <= max_outputs <= 50:
            raise ValueError("max_outputs must be between 1 and 50")
        if len(observations) < 2:
            raise ValueError("FailureDedupAgent requires at least two observations")
        if not self.model.enabled:
            raise RuntimeError("The Failure Dedup model is not configured")

        rendered = _input_text(
            operation_key=operation_key,
            observations=observations,
        )
        context = AgentContext(
            system=self.system_prompt,
            user=rendered.text,
            limits=ContextLimits(
                system_chars=3_500,
                initial_user_chars=24_000,
                feedback_chars=8_000,
                conversation_chars=40_000,
                required_output_tokens=self.model.max_tokens,
            ),
            metrics=rendered.metrics,
        )
        last_errors: list[str] = []
        correction_count = 0
        tools = self._build_tools(catalog=catalog)

        with self.tracing_runtime.span(
            "FailureDedupAgent.deduplicate",
            kind="AGENT",
            input_value={
                "operation_key": operation_key,
                "observation_count": len(observations),
            },
        ) as span:
            for output_number in range(1, max_outputs + 1):
                response = self.client.invoke(
                    LLMRequest(
                        provider=self.model.provider,
                        model=self.model.model,
                        messages=context.messages_for_request(self.model),
                        temperature=0,
                        max_tokens=self.model.max_tokens,
                        response_format="json",
                        tools=tools.specs(),
                        tool_choice="auto",
                        timeout_seconds=self.model.timeout_seconds,
                        reasoning=self.model.reasoning,
                        metadata={"role": "operation_smoke_failure_dedup"},
                    )
                )
                if response.tool_calls:
                    errors = _tool_call_errors(
                        response,
                        allowed_names={spec.name for spec in tools.specs()},
                    )
                    if errors:
                        correction_count += 1
                        last_errors = errors
                        context.append_feedback(_correction_text(errors))
                        continue
                    context.append_assistant(response)
                    for result in tools.execute_many(response.tool_calls):
                        context.append_tool_result(
                            result.name,
                            result.tool_call_id,
                            tool_result_json(result),
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
                            supplied_messages=[
                                str(item["message"]) for item in observations
                            ],
                            semantic_parameters=set(semantic_parameters),
                        )
                    )
                if not errors and decision is not None:
                    canonical = _canonicalize(decision)
                    span.set_output(
                        {
                            "failure_count": len(canonical.failures),
                            "outputs_used": output_number,
                            "correction_count": correction_count,
                        }
                    )
                    return canonical, output_number, correction_count, []

                last_errors = errors or [
                    "The response was not a usable FailureDedupDecision."
                ]
                correction_count += 1
                context.append_assistant(response)
                context.append_feedback(_correction_text(last_errors))

            span.set_output(
                {
                    "status": "dedup_budget_exhausted",
                    "outputs_used": max_outputs,
                    "correction_count": correction_count,
                }
            )
            return None, max_outputs, correction_count, last_errors

    def _build_tools(
        self,
        *,
        catalog: TestCaseCatalog,
    ) -> AgentToolbox:
        """Bind shared and run-local implementations for one Dedup call."""
        tools = AgentToolbox(tracing_runtime=self.tracing_runtime)
        tools.register(
            spec=openapi_list_inputs_tool_spec(),
            execute=self.openapi_capability.list_inputs,
        )
        tools.register(
            spec=catalog_query_tool_spec(),
            execute=lambda **arguments: {
                "structured": query_catalog(
                    catalog=catalog,
                    arguments=arguments,
                )
            },
        )
        return tools


def _input_text(
    *,
    operation_key: str,
    observations: list[dict[str, Any]],
):
    """Render only Failure Messages and representative Catalog references."""
    writer = CompactTextWriter(max_value_chars=4_096)
    writer.section("Operation")
    writer.text("operation", operation_key)
    writer.section("Current Failure Cases", untrusted=True)
    for observation in observations:
        writer.record(
            str(observation["case_id"]),
            failure_message=str(observation["message"]),
        )
    return writer.render(max_chars=24_000)


def _tool_call_errors(
    response: LLMResponse,
    *,
    allowed_names: set[str],
) -> list[str]:
    """Reject mixed, duplicate, or unavailable calls before batch execution."""
    if response.parsed_json is not None:
        return ["Do not mix a tool call with a final JSON decision."]
    call_ids = [call.id for call in response.tool_calls]
    if len(call_ids) != len(set(call_ids)):
        return ["Every Failure Dedup tool call must have a unique call id."]
    for call in response.tool_calls:
        if call.name not in allowed_names:
            return [f"Unknown Failure Dedup tool: {call.name}"]
    return []


def _parse_decision(
    response: LLMResponse,
    *,
    validator: OutputValidator,
) -> tuple[FailureDedupDecision | None, list[str]]:
    """Parse the provider response through the shared strict JSON validator."""
    result = validator.validate(
        response=response,
        output_model=FailureDedupDecision,
    )
    if not result.valid:
        return None, [
            (
                f"{issue.location}: {issue.message}"
                if issue.location
                else issue.message
            )
            for issue in result.errors
        ]
    return FailureDedupDecision.model_validate(result.validated_object), []


def _semantic_errors(
    decision: FailureDedupDecision,
    *,
    supplied_messages: list[str],
    semantic_parameters: set[str],
) -> list[str]:
    """Reject forged facts, incomplete coverage, and duplicate Parameter groups."""
    errors: list[str] = []
    supplied = set(supplied_messages)
    seen_messages: list[str] = []
    nonempty_sets: set[tuple[str, ...]] = set()

    for group in decision.failures:
        unknown = sorted(set(group.suspected_parameters) - semantic_parameters)
        for handle in unknown:
            errors.append(f"Unknown semantic Parameter: {handle}")
        parameter_set = tuple(sorted(set(group.suspected_parameters)))
        if parameter_set and parameter_set in nonempty_sets:
            errors.append(
                "Failures with the same complete non-empty suspected Parameter "
                f"set must be merged: {list(parameter_set)}"
            )
        nonempty_sets.add(parameter_set) if parameter_set else None
        for message in group.messages:
            if message not in supplied:
                errors.append(f"Message was not supplied exactly: {message}")
            seen_messages.append(message)

    for message in supplied_messages:
        count = seen_messages.count(message)
        if count == 0:
            errors.append(f"Missing input message: {message}")
        elif count > 1:
            errors.append(f"Message appears more than once: {message}")
    return errors


def _canonicalize(decision: FailureDedupDecision) -> FailureDedupDecision:
    """Sort and deduplicate Parameter handles without spending an LLM retry."""
    return FailureDedupDecision(
        failures=[
            {
                "summary": group.summary,
                "suspected_parameters": sorted(set(group.suspected_parameters)),
                "messages": group.messages,
            }
            for group in decision.failures
        ],
        reason=decision.reason,
    )


def _correction_text(errors: list[str]) -> str:
    """Encode deterministic problems as Markdown without internal references."""
    writer = CompactTextWriter(max_value_chars=1_000)
    writer.section("Correction Required")
    writer.text(
        "result",
        "Your previous FailureDedupDecision was rejected.",
    )
    writer.section("Problems", untrusted=True)
    for error in errors:
        writer.text("problem", error)
    writer.section("Required Fix")
    writer.text(
        "instruction",
        "Return one complete replacement FailureDedupDecision JSON object. "
        "Copy every supplied message exactly once, use only semantic Parameter "
        "handles returned by OpenAPI lookup, merge equal non-empty Parameter "
        "sets, and do not "
        "return IDs, fingerprints, test cases, prose, or a partial correction.",
    )
    return writer.render(max_chars=8_000).text
