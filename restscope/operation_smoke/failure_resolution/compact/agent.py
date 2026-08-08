"""Summarize one complete Resolution history before local compaction.

This Agent receives the existing Resolution system contract ``B``, the full
saved conversation ``H``, and a temporary checkpoint instruction ``C``. It
returns Markdown ``S`` only. The parent Resolution session owns whether and
when that summary replaces the old history.
"""

from __future__ import annotations

from restscope.agent import AgentProfile
from restscope.context import AgentContext
from restscope.llm import (
    LLMClient,
    LLMError,
    LLMModelConfig,
    LLMReasoningConfig,
    LLMRequest,
)
from restscope.observability import TracingRuntime
from restscope.operation_smoke.output_limit import ModelOutputLimit

from .prompts import COMPACT_INSTRUCTION


MODEL_ROLE = "operation_smoke_failure_resolution_compact"
_MAX_ATTEMPTS = 2
_PROFILE = AgentProfile(
    name="failure_resolution_compact",
    model_config_name="fast",
)


class FailureResolutionCompactError(RuntimeError):
    """Report that both bounded Compact attempts failed to produce Markdown."""


class FailureResolutionCompactAgent:
    """Create one Markdown handoff from the complete current Resolution history."""

    def __init__(
        self,
        *,
        client: LLMClient,
        model: LLMModelConfig,
        tracing_runtime: TracingRuntime | None = None,
    ) -> None:
        """Store the FAST model and trace runtime used for local compaction."""
        self.client = client
        self.model = model
        self.profile = _PROFILE
        self.tracing_runtime = tracing_runtime or TracingRuntime.disabled()

    def run(
        self,
        *,
        context: AgentContext,
        output_limit: ModelOutputLimit,
    ) -> str:
        """Return summary ``S`` without mutating the source Resolution Context.

        Args:
            context: The Resolution Context that owns immutable system prompt
                ``B`` and complete replaceable history ``H``.
            output_limit: Operation-wide guard shared by Resolution, Patch,
                Review, and Compact model calls.

        Returns:
            Non-empty Markdown produced from temporary ``B + H + C`` messages.

        Raises:
            RuntimeError: If the FAST model is disabled.
            FailureResolutionCompactError: If two attempts fail or return no
                usable Markdown.
            ModelOutputLimitExceeded: If no Operation-wide output remains.
        """
        if not self.model.enabled:
            raise RuntimeError("The Failure Resolution Compact model is not configured")

        compact_messages = context.messages_for_compaction(COMPACT_INSTRUCTION)
        with self.tracing_runtime.span(
            "FailureResolutionCompactAgent.run",
            kind="AGENT",
            input_value={"history_message_count": len(context.clone_history())},
        ) as span:
            latest_error: Exception | None = None
            for attempt_number in range(1, _MAX_ATTEMPTS + 1):
                output_limit.consume(MODEL_ROLE)
                try:
                    response = self.client.invoke(
                        LLMRequest(
                            provider=self.model.provider,
                            model=self.model.model,
                            messages=[
                                message.model_copy(deep=True)
                                for message in compact_messages
                            ],
                            temperature=0,
                            max_tokens=self.model.max_tokens,
                            response_format="text",
                            tools=[],
                            tool_choice="none",
                            timeout_seconds=self.model.timeout_seconds,
                            reasoning=LLMReasoningConfig(mode="disabled"),
                            metadata={"role": MODEL_ROLE},
                        )
                    )
                except LLMError as exc:
                    latest_error = exc
                    continue

                compact_summary = response.content or ""
                if not compact_summary.strip():
                    latest_error = RuntimeError(
                        "Compact model returned an empty Markdown summary"
                    )
                    continue
                span.set_attribute(
                    "restscope.resolution.compact.attempt_count",
                    attempt_number,
                )
                span.set_output({"summary_chars": len(compact_summary)})
                return compact_summary

            raise FailureResolutionCompactError(
                "Failure Resolution Compact failed after two attempts"
            ) from latest_error
