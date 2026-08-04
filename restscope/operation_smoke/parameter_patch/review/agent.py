"""Independently review one compiled Parameter Patch inside its owning Module.

This Agent sees normalized candidate facts but never the Patch Agent's dialogue,
compiler errors, earlier Reviewer issues, or hidden reasoning. It owns only the
semantic pass/reject decision for this one candidate. The enclosing Parameter
Patch Coordinator is its only production caller.
"""

from __future__ import annotations

import hashlib
import json

from restscope.context import AgentContext, CompactTextWriter, ContextLimits
from restscope.llm import (
    LLMClient,
    LLMModelConfig,
    LLMReasoningConfig,
    LLMRequest,
    LLMResponse,
    OutputValidator,
)
from restscope.observability import TracingRuntime

from .prompts import build_parameter_patch_review_prompt
from .schemas import (
    ParameterPatchReviewCandidate,
    ParameterPatchReviewFailure,
    ParameterPatchReviewResult,
    ParameterPatchReviewSubmission,
)


_MAX_ERRORS = 20


class ParameterPatchReviewAgent:
    """Review one candidate with structured output and bounded correction."""

    def __init__(
        self,
        *,
        client: LLMClient,
        model: LLMModelConfig,
        system_prompt: str | None = None,
        validator: OutputValidator | None = None,
        tracing_runtime: TracingRuntime | None = None,
    ) -> None:
        """Store immutable model and validation collaborators for one fresh run."""
        self.client = client
        self.model = model
        self.system_prompt = system_prompt
        self.validator = validator or OutputValidator()
        self.tracing_runtime = tracing_runtime or TracingRuntime.disabled()

    def run(
        self,
        candidate: ParameterPatchReviewCandidate,
        *,
        max_outputs: int,
        shared_outputs_used: int,
    ) -> ParameterPatchReviewResult | ParameterPatchReviewFailure:
        """Review one candidate without importing any prior Agent conversation.

        Invalid Reviewer protocol is corrected in this same fresh context. The
        The model returns only concrete issues. Runtime code derives acceptance
        from whether that list is empty, so contradictory verdict fields cannot
        enter the Parameter Patch flow.
        """
        if not 1 <= max_outputs <= 20:
            raise ValueError("max_outputs must be between 1 and 20")
        if not self.model.enabled:
            raise RuntimeError("The Parameter Patch Review model is not configured")
        prompt = build_parameter_patch_review_prompt(
            candidate,
            system_prompt=self.system_prompt,
        )
        context = AgentContext(
            system=prompt.system,
            user=prompt.user,
            limits=ContextLimits(
                system_chars=5_000,
                initial_user_chars=24_000,
                feedback_chars=8_000,
                conversation_chars=34_000,
                required_output_tokens=self.model.max_tokens,
            ),
            metrics=prompt.metrics,
        )
        attempts: list[dict] = []
        latest_errors: list[str] = []
        last_fingerprint: str | None = None
        repeated_count = 0

        with self.tracing_runtime.span(
            "ParameterPatchReviewAgent.run",
            kind="AGENT",
            input_value={
                "affected_input_count": len(candidate.affected_inputs),
                "max_outputs": max_outputs,
            },
            attributes={
                "restscope.patch.shared_outputs_used": shared_outputs_used,
            },
        ) as span:
            for output_number in range(1, max_outputs + 1):
                response = self._invoke(context)
                submission, errors = self._parse(response)
                transport = "json_schema"
                span.set_attribute(
                    "restscope.patch.review.transport",
                    transport,
                )
                attempts.append(
                    {
                        "content": response.content,
                        "parsed_json": response.parsed_json,
                        "tool_calls": [
                            {"name": call.name, "arguments": call.arguments}
                            for call in response.tool_calls
                        ],
                        "transport": transport,
                    }
                )
                if submission is not None:
                    issues = list(submission.issues)
                    result = ParameterPatchReviewResult(
                        accepted=not issues,
                        issues=issues,
                        outputs_used=output_number,
                        attempt_history=attempts,
                    )
                    span.set_attribute("restscope.patch.review.issue_count", len(issues))
                    span.set_attribute(
                        "restscope.patch.shared_outputs_used",
                        shared_outputs_used + output_number,
                    )
                    span.set_output(
                        {"accepted": result.accepted, "issue_count": len(issues)}
                    )
                    return result

                latest_errors = errors or ["The Review output could not be used."]
                fingerprint = _invalid_fingerprint(response, latest_errors)
                if fingerprint == last_fingerprint:
                    repeated_count += 1
                else:
                    last_fingerprint = fingerprint
                    repeated_count = 1
                if repeated_count >= 3:
                    return ParameterPatchReviewFailure(
                        reason="repeated_invalid_output",
                        outputs_used=output_number,
                        errors=latest_errors,
                        attempt_history=attempts,
                    )
                _append_correction(
                    context,
                    response,
                    _invalid_review_feedback(latest_errors),
                )

            return ParameterPatchReviewFailure(
                reason="output_budget_exhausted",
                outputs_used=max_outputs,
                errors=latest_errors,
                attempt_history=attempts,
            )

    def _invoke(self, context: AgentContext) -> LLMResponse:
        """Request one issue list through the provider's JSON Schema boundary."""
        return self.client.invoke(
            LLMRequest(
                provider=self.model.provider,
                model=self.model.model,
                messages=context.messages_for_request(self.model),
                temperature=0,
                max_tokens=self.model.max_tokens,
                timeout_seconds=self.model.timeout_seconds,
                metadata={"role": "parameter_patch_review_agent"},
                reasoning=LLMReasoningConfig(mode="disabled"),
                response_format="json_schema",
                json_schema=ParameterPatchReviewSubmission.model_json_schema(),
                json_schema_name="ParameterPatchReviewSubmission",
                tools=[],
                tool_choice="none",
            )
        )

    def _parse(
        self,
        response: LLMResponse,
    ) -> tuple[ParameterPatchReviewSubmission | None, list[str]]:
        """Validate one structured Review issue list."""
        validated = self.validator.validate(
            response=response,
            output_model=ParameterPatchReviewSubmission,
        )
        if not validated.valid:
            return None, [
                f"{issue.location}: {issue.message}"
                if issue.location
                else issue.message
                for issue in validated.errors[:_MAX_ERRORS]
            ]
        return (
            ParameterPatchReviewSubmission.model_validate(
                validated.validated_object
            ),
            [],
        )


def _append_correction(
    context: AgentContext,
    response: LLMResponse,
    text: str,
) -> None:
    """Preserve the invalid JSON response before appending trusted guidance."""
    # No Review tool was offered or executed. Discard any unexpected provider
    # tool call so the next request never contains an orphan tool-call group.
    if not response.tool_calls:
        context.append_assistant(response)
    context.append_feedback(text)


def _invalid_review_feedback(errors: list[str]) -> str:
    """Render untrusted validation errors beside a trusted fixed Review shape."""
    writer = CompactTextWriter(max_value_chars=800)
    writer.section(
        "REASONS THE PREVIOUS REVIEW OUTPUT WAS REJECTED",
        untrusted=True,
    )
    for index, error in enumerate(errors[:_MAX_ERRORS], start=1):
        writer.text(f"issue {index}", error)
    writer.section("REQUIRED REPLACEMENT REVIEW")
    writer.text("next", "Return one JSON object matching the response schema.")
    writer.text("fields", "Provide only issues:array of strings.")
    return writer.render(max_chars=8_000).text


def _invalid_fingerprint(response: LLMResponse, errors: list[str]) -> str:
    """Identify three repeated invalid Review protocol outputs."""
    value = [
        {"name": call.name, "arguments": call.arguments}
        for call in response.tool_calls
    ] or response.parsed_json or response.content
    normalized = json.dumps(
        {"candidate": value, "errors": errors},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(normalized.encode()).hexdigest()
