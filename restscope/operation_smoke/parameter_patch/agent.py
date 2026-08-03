"""Ask the model for complete Parameter Patch proposals.

The Patch Agent owns one bounded revision conversation for a Solve requirement.
It returns typed proposals and accepts deterministic compile or review feedback;
it does not compile, sample, review, persist, or accept a candidate itself.
"""

from __future__ import annotations

from dataclasses import dataclass

from restscope.context import AgentContext, ContextLimits
from restscope.llm import (
    LLMClient,
    LLMModelConfig,
    LLMReasoningConfig,
    LLMRequest,
    LLMResponse,
    OutputValidator,
    StrictToolUnavailableError,
    ToolCall,
)
from restscope.observability import TracingRuntime

from .decision_tool import (
    PARAMETER_PATCH_PROPOSAL_TOOL,
    parameter_patch_proposal_tool_spec,
)
from .prompts import ParameterPatchPrompt
from .schemas import ParameterPatchSubmission


_MAX_ERRORS = 20


@dataclass(slots=True, frozen=True)
class ParameterPatchAttempt:
    """Return one model output plus its typed proposal or protocol errors."""

    response: LLMResponse
    submission: ParameterPatchSubmission | None
    errors: list[str]
    transport: str


class ParameterPatchAgent:
    """Maintain one proposal/revision context and call the Patch model."""

    def __init__(
        self,
        *,
        client: LLMClient,
        model: LLMModelConfig,
        prompt: ParameterPatchPrompt,
        validator: OutputValidator | None = None,
        tracing_runtime: TracingRuntime | None = None,
    ) -> None:
        """Create an isolated proposal session from a bounded domain prompt.

        Args:
            client: Provider-neutral model client.
            model: FAST model selected for Patch proposals.
            prompt: Initial Solve requirement and runtime-only reference aliases.
            validator: Optional structured-output validator used by tests.
            tracing_runtime: Trace sink; sensitive prompt and reasoning stay out.
        """
        self.client = client
        self.model = model
        self.validator = validator or OutputValidator()
        self.tracing_runtime = tracing_runtime or TracingRuntime.disabled()
        self.reference_by_alias = prompt.reference_by_alias
        self.context = AgentContext(
            system=prompt.system,
            user=prompt.user,
            limits=ContextLimits(
                system_chars=7_000,
                initial_user_chars=18_000,
                feedback_chars=12_000,
                conversation_chars=36_000,
                required_output_tokens=model.max_tokens,
            ),
            metrics=prompt.metrics,
        )
        self.legacy_json_mode = False

    def propose(self, *, shared_output_number: int) -> ParameterPatchAttempt:
        """Request one full proposal and validate its transport-level shape.

        A strict transport compatibility failure switches this session once to
        legacy JSON and does not itself consume a model output. Provider errors
        outside that narrow category propagate to the owning Coordinator.
        """
        if not self.model.enabled:
            raise RuntimeError("The Parameter Patch model is not configured")
        fallback_reason: str | None = None
        with self.tracing_runtime.span(
            "ParameterPatchAgent.propose",
            kind="AGENT",
            input_value={"shared_output_number": shared_output_number},
            attributes={
                "restscope.patch.shared_output_number": shared_output_number,
                "restscope.patch.strict_tool_requested": True,
                "restscope.patch.strict_fallback_used": False,
            },
        ) as span:
            try:
                response = self._invoke()
            except StrictToolUnavailableError as exc:
                self.legacy_json_mode = True
                fallback_reason = exc.code
                span.set_attribute("restscope.patch.strict_fallback_used", True)
                span.set_attribute("restscope.patch.strict_fallback_reason", exc.code)
                response = self._invoke()

            submission, errors = self._parse(response)
            transport = "legacy_json" if self.legacy_json_mode else "strict_tool"
            span.set_output(
                {
                    "valid": submission is not None,
                    "error_count": len(errors),
                    "transport": transport,
                    "fallback_reason": fallback_reason,
                }
            )
            return ParameterPatchAttempt(
                response=response,
                submission=submission,
                errors=errors,
                transport=transport,
            )

    def append_feedback(self, attempt: ParameterPatchAttempt, text: str) -> None:
        """Return bounded compiler or Reviewer feedback to this same session.

        Strict tool calls are paired with a matching tool result. Invalid tool
        groups cannot be replayed safely, so their correction is a user message.
        Legacy JSON likewise uses an assistant message followed by feedback.
        """
        call = _submission_call(attempt.response)
        if not self.legacy_json_mode and call is not None:
            self.context.append_assistant(attempt.response)
            self.context.append_tool_result(call.name, call.id, text)
            return
        if self.legacy_json_mode:
            self.context.append_assistant(attempt.response)
        self.context.append_feedback(text)

    def _invoke(self) -> LLMResponse:
        """Call strict submission or the session's one-way legacy fallback."""
        common = {
            "provider": self.model.provider,
            "model": self.model.model,
            "messages": self.context.messages_for_request(self.model),
            "temperature": 0,
            "max_tokens": self.model.max_tokens,
            "timeout_seconds": self.model.timeout_seconds,
            "metadata": {"role": "parameter_patch_agent"},
            "reasoning": LLMReasoningConfig(mode="disabled"),
        }
        if self.legacy_json_mode:
            return self.client.invoke(
                LLMRequest(
                    **common,
                    response_format="json_schema",
                    json_schema=ParameterPatchSubmission.model_json_schema(),
                    json_schema_name="ParameterPatchSubmission",
                    tools=[],
                    tool_choice="none",
                )
            )
        return self.client.invoke(
            LLMRequest(
                **common,
                response_format="text",
                tools=[parameter_patch_proposal_tool_spec()],
                tool_choice="required",
            )
        )

    def _parse(
        self,
        response: LLMResponse,
    ) -> tuple[ParameterPatchSubmission | None, list[str]]:
        """Convert one exact proposal tool call or legacy JSON object to a DTO."""
        candidate = response
        if not self.legacy_json_mode:
            if len(response.tool_calls) != 1:
                return None, [
                    "exactly one submit_parameter_patch_proposal tool call is required"
                ]
            call = response.tool_calls[0]
            if call.name != PARAMETER_PATCH_PROPOSAL_TOOL:
                return None, [f"unexpected Patch proposal tool: {call.name}"]
            candidate = response.model_copy(update={"parsed_json": call.arguments})
        result = self.validator.validate(
            response=candidate,
            output_model=ParameterPatchSubmission,
        )
        if not result.valid:
            return None, [
                f"{issue.location}: {issue.message}"
                if issue.location
                else issue.message
                for issue in result.errors[:_MAX_ERRORS]
            ]
        return ParameterPatchSubmission.model_validate(result.validated_object), []


def _submission_call(response: LLMResponse) -> ToolCall | None:
    """Return the sole correctly named proposal call, if one exists."""
    if len(response.tool_calls) != 1:
        return None
    call = response.tool_calls[0]
    return call if call.name == PARAMETER_PATCH_PROPOSAL_TOOL else None
