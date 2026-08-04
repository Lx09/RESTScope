"""Ask the model for complete Parameter Patch proposals.

The Patch Agent owns one bounded revision conversation for a Solve requirement.
It returns typed proposals and accepts deterministic compile or review feedback;
it does not compile, sample, review, persist, or accept a candidate itself.
"""

from __future__ import annotations

from dataclasses import dataclass
import json

from restscope.context import AgentContext, ContextLimits
from restscope.llm import (
    LLMClient,
    LLMModelConfig,
    LLMReasoningConfig,
    LLMRequest,
    LLMResponse,
    OutputValidator,
)
from restscope.observability import TracingRuntime

from .prompts import ParameterPatchPrompt
from .schemas import ParameterPatchSubmission


_MAX_ERRORS = 20
_MAX_STRUCTURED_JSON_CHARS = 65_536
_MAX_INSERTED_DELIMITERS = 8


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
    def propose(self, *, shared_output_number: int) -> ParameterPatchAttempt:
        """Request one full proposal and validate its transport-level shape.

        The provider receives the same recursive DTO Schema used by local
        validation. Provider errors propagate to the owning Coordinator.
        """
        if not self.model.enabled:
            raise RuntimeError("The Parameter Patch model is not configured")
        with self.tracing_runtime.span(
            "ParameterPatchAgent.propose",
            kind="AGENT",
            input_value={"shared_output_number": shared_output_number},
            attributes={
                "restscope.patch.shared_output_number": shared_output_number,
            },
        ) as span:
            response = self._invoke()
            submission, errors = self._parse(response)
            transport = "json_schema"
            span.set_output(
                {
                    "valid": submission is not None,
                    "error_count": len(errors),
                    "transport": transport,
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

        The invalid structured response remains in the conversation, followed
        by trusted compiler or Reviewer feedback requesting one replacement.
        """
        # A tool call was never offered or executed, so replaying an unexpected
        # provider call would create an orphan assistant/tool group.
        if not attempt.response.tool_calls:
            self.context.append_assistant(attempt.response)
        self.context.append_feedback(text)

    def _invoke(self) -> LLMResponse:
        """Request one proposal through the provider's JSON Schema boundary."""
        return self.client.invoke(
            LLMRequest(
                provider=self.model.provider,
                model=self.model.model,
                messages=self.context.messages_for_request(self.model),
                temperature=0,
                max_tokens=self.model.max_tokens,
                timeout_seconds=self.model.timeout_seconds,
                metadata={"role": "parameter_patch_agent"},
                reasoning=LLMReasoningConfig(mode="disabled"),
                response_format="json_schema",
                json_schema=ParameterPatchSubmission.model_json_schema(),
                json_schema_name="ParameterPatchSubmission",
                tools=[],
                tool_choice="none",
            )
        )

    def _parse(
        self,
        response: LLMResponse,
    ) -> tuple[ParameterPatchSubmission | None, list[str]]:
        """Convert one structured response into the Proposal DTO."""
        candidate = _structured_candidate(response)
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


def _structured_candidate(response: LLMResponse) -> LLMResponse:
    """Parse one narrowly repairable structured JSON object before validation.

    DeepSeek occasionally returns an otherwise complete Patch with one closing
    object delimiter omitted. The repair only inserts delimiters uniquely
    implied by the existing bracket stack. It never changes names, values,
    quotes, commas, or provider text, and the resulting object still passes the
    normal DTO, compiler, sampling, and Review boundaries.
    """
    if response.parsed_json is not None or response.content is None:
        return response
    repaired = _complete_truncated_json_object(response.content)
    if repaired is None:
        return response
    try:
        parsed = json.loads(repaired)
    except json.JSONDecodeError:
        return response
    return response.model_copy(update={"parsed_json": parsed})


def _complete_truncated_json_object(text: str) -> str | None:
    """Insert at most eight uniquely implied closing braces or brackets."""
    source = text.strip()
    if (
        not source.startswith("{")
        or len(source) > _MAX_STRUCTURED_JSON_CHARS
    ):
        return None

    output: list[str] = []
    stack: list[str] = []
    closing_for = {"{": "}", "[": "]"}
    opening_for = {"}": "{", "]": "["}
    in_string = False
    escaped = False
    inserted = 0
    root_complete = False

    for character in source:
        if root_complete:
            # Any non-whitespace suffix is provider text, not truncated JSON.
            if not character.isspace():
                return None
            output.append(character)
            continue
        if in_string:
            output.append(character)
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            continue
        if character == '"':
            in_string = True
            output.append(character)
            continue
        if character in closing_for:
            stack.append(character)
            output.append(character)
            continue
        if character in opening_for:
            while stack and stack[-1] != opening_for[character]:
                inserted += 1
                if inserted > _MAX_INSERTED_DELIMITERS:
                    return None
                output.append(closing_for[stack.pop()])
            if not stack:
                return None
            stack.pop()
            output.append(character)
            root_complete = not stack
            continue
        output.append(character)

    if in_string:
        return None
    while stack:
        inserted += 1
        if inserted > _MAX_INSERTED_DELIMITERS:
            return None
        output.append(closing_for[stack.pop()])
    return "".join(output)
