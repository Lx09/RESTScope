"""Investigate one failure todo through a continuous model conversation.

The session exposes the current-operation HTTP tool, keeps every observation
in the same conversation, and returns control only when a Patch requirement or
terminal todo decision is ready. Patch and Effect feedback can then resume the
same session without reconstructing or losing its investigation.
"""

from __future__ import annotations

import json
from typing import Protocol

from restscope.llm import (
    LLMClient,
    LLMMessage,
    LLMModelConfig,
    LLMRequest,
    OutputValidator,
    ToolCall,
    ToolResult,
    ToolSpec,
)
from restscope.agent.prompt_context import (
    fit_message_context,
    fit_prompt_context,
)
from restscope.testing import OperationGeneratorConfig

from .schemas import (
    FailureSolveDecision,
    FailureSolveOutcome,
    FailureSolveRequest,
)


class HTTPProbe(Protocol):
    """Describe the scoped HTTP collaborator available to Solve."""

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


class FailureSolveAgent:
    """Create one independent, continuous Solve session per failure todo."""

    def __init__(
        self,
        *,
        client: LLMClient,
        model: LLMModelConfig,
        http_probe: HTTPProbe,
        validator: OutputValidator | None = None,
    ) -> None:
        """Store immutable collaborators shared by otherwise isolated sessions."""
        self.client = client
        self.model = model
        self.http_probe = http_probe
        self.validator = validator or OutputValidator()

    def start(
        self,
        request: FailureSolveRequest,
        *,
        config: OperationGeneratorConfig,
        max_outputs: int = 50,
        continuation_interval: int = 10,
    ) -> "FailureSolveSession":
        """Start a todo-local conversation with complete expanded evidence."""
        if not 1 <= max_outputs <= 50:
            raise ValueError("max_outputs must be between 1 and 50")
        if continuation_interval < 1:
            raise ValueError("continuation_interval must be positive")
        if not self.model.enabled:
            raise RuntimeError("The Failure Solve model is not configured")
        return FailureSolveSession(
            client=self.client,
            model=self.model,
            http_probe=self.http_probe,
            validator=self.validator,
            request=request,
            config=config,
            max_outputs=max_outputs,
            continuation_interval=continuation_interval,
        )


class FailureSolveSession:
    """Retain messages, observations, and output count for one todo."""

    def __init__(
        self,
        *,
        client: LLMClient,
        model: LLMModelConfig,
        http_probe: HTTPProbe,
        validator: OutputValidator,
        request: FailureSolveRequest,
        config: OperationGeneratorConfig,
        max_outputs: int,
        continuation_interval: int,
    ) -> None:
        """Initialize a session without making a model or HTTP call."""
        self.client = client
        self.model = model
        self.http_probe = http_probe
        self.validator = validator
        self.config = config
        self.max_outputs = max_outputs
        self.continuation_interval = continuation_interval
        self.outputs_used = 0
        self.observations: list[dict] = []
        self.output_history: list[dict] = []
        fitted = fit_prompt_context(
            required=request.model_dump(
                mode="json",
                exclude={"history"},
            ),
            history=request.history,
            model=model,
        )
        self.messages = [
            LLMMessage(
                role="system",
                content=(
                    "Investigate exactly one Operation Smoke failure. You own "
                    "the root-cause and patch-requirement decisions. Use the "
                    "current-operation HTTP tool when more evidence is useful. "
                    "When ready, return action=patch_ready with root_cause, "
                    "affected_inputs, desired_behavior, and "
                    "acceptance_criteria. If work should end, return "
                    "action=finish with a supplied finish_status and reason. "
                    "Do not use temporary evidence codes."
                ),
            ),
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

    def advance(self, *, feedback: dict | None = None) -> FailureSolveOutcome:
        """Continue until Patch handoff, terminal status, or output exhaustion.

        ``feedback`` contains a rejected Patch or Effect result from the
        coordinator. It is appended to this same conversation before another
        model output is requested.
        """
        if feedback is not None:
            self.messages.append(
                LLMMessage(
                    role="user",
                    content=(
                        "The previous Patch attempt was not accepted. Continue "
                        "the same investigation using this complete feedback:\n"
                        + json.dumps(
                            feedback,
                            ensure_ascii=False,
                            separators=(",", ":"),
                            default=str,
                        )
                    ),
                )
            )

        while self.outputs_used < self.max_outputs:
            next_output = self.outputs_used + 1
            checkpoint = (
                next_output % self.continuation_interval == 0
                and next_output < self.max_outputs
            )
            if checkpoint:
                self.messages.append(
                    LLMMessage(
                        role="user",
                        content=(
                            "Continuation checkpoint: decide whether to "
                            "continue or stop this failure attempt. Return "
                            "action=continue with a genuinely new next_step, or "
                            "action=finish with a terminal status and reason. "
                            "No HTTP tool is available for this output."
                        ),
                    )
                )
            tools = (
                []
                if checkpoint
                else [self.http_probe.tool_spec(self.config)]
            )
            response = self.client.invoke(
                LLMRequest(
                    provider=self.model.provider,
                    model=self.model.model,
                    messages=fit_message_context(
                        self.messages,
                        model=self.model,
                    ).messages,
                    temperature=0,
                    max_tokens=self.model.max_tokens,
                    response_format="json",
                    tools=tools,
                    tool_choice="none" if checkpoint else "auto",
                    timeout_seconds=self.model.timeout_seconds,
                    reasoning=self.model.reasoning,
                    metadata={"role": "operation_smoke_failure_solve"},
                )
            )
            self.outputs_used += 1
            self.output_history.append(_response_record(response))

            if response.tool_calls:
                errors = _tool_errors(
                    response,
                    checkpoint=checkpoint,
                    probe=self.http_probe,
                    config=self.config,
                )
                if errors:
                    self._append_correction(response, errors)
                    continue
                self.messages.append(
                    LLMMessage(
                        role="assistant",
                        content="",
                        tool_calls=response.tool_calls,
                    )
                )
                # All calls were checked first, so one invalid call can never
                # leave a partially executed external batch.
                for tool_call in response.tool_calls:
                    result = self.http_probe.execute(
                        config=self.config,
                        tool_call=tool_call,
                    )
                    record = result.model_dump(mode="json")
                    self.observations.append(record)
                    self.messages.append(
                        LLMMessage(
                            role="tool",
                            name=result.name,
                            tool_call_id=result.tool_call_id,
                            content=json.dumps(
                                record,
                                ensure_ascii=False,
                                separators=(",", ":"),
                                default=str,
                            ),
                        )
                    )
                continue

            decision, errors = self._decision(response)
            if decision is not None and checkpoint:
                if decision.action not in {"continue", "finish"}:
                    errors.append(
                        "A continuation checkpoint allows continue or stop only."
                    )
            elif decision is not None and decision.action == "continue":
                errors.append(
                    "action=continue is available only at a continuation checkpoint."
                )
            if errors or decision is None:
                self._append_correction(response, errors)
                continue

            self.messages.append(
                LLMMessage(
                    role="assistant",
                    content=json.dumps(
                        decision.model_dump(
                            mode="json",
                            exclude_none=True,
                        ),
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                )
            )
            if decision.action == "continue":
                self.messages.append(
                    LLMMessage(
                        role="user",
                        content=(
                            "Continue with the stated new next_step. The "
                            "current-operation HTTP tool is available again."
                        ),
                    )
                )
                continue
            if decision.action == "patch_ready":
                return FailureSolveOutcome(
                    status="patch_ready",
                    outputs_used=self.outputs_used,
                    patch_requirement=decision.patch_requirement,
                    observations=list(self.observations),
                    output_history=list(self.output_history),
                )
            assert decision.finish_status is not None
            return FailureSolveOutcome(
                status=decision.finish_status,
                outputs_used=self.outputs_used,
                reason=decision.reason,
                observations=list(self.observations),
                output_history=list(self.output_history),
            )

        return FailureSolveOutcome(
            status="solve_budget_exhausted",
            outputs_used=self.outputs_used,
            reason="The Failure Solve output budget was exhausted.",
            observations=list(self.observations),
            output_history=list(self.output_history),
        )

    def _decision(
        self,
        response,
    ) -> tuple[FailureSolveDecision | None, list[str]]:
        """Parse one strict non-tool response."""
        validation = self.validator.validate(
            response=response,
            output_model=FailureSolveDecision,
        )
        if not validation.valid:
            return None, [
                (
                    f"{issue.location}: {issue.message}"
                    if issue.location
                    else issue.message
                )
                for issue in validation.errors
            ]
        return (
            FailureSolveDecision.model_validate(validation.validated_object),
            [],
        )

    def _append_correction(self, response, errors: list[str]) -> None:
        """Keep invalid output and concrete correction guidance in-session."""
        self.messages.extend(
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
                        "The previous output could not be used:\n"
                        + "\n".join(f"- {error}" for error in errors)
                        + "\nContinue this same investigation with one valid "
                        "tool output or one complete decision."
                    ),
                ),
            )
        )


def _tool_errors(
    response,
    *,
    checkpoint: bool,
    probe: HTTPProbe,
    config: OperationGeneratorConfig,
) -> list[str]:
    """Prevalidate the complete tool batch before executing any request."""
    errors: list[str] = []
    if checkpoint:
        errors.append("HTTP tools are unavailable at a continuation checkpoint.")
    if response.parsed_json is not None or (
        response.content is not None and response.content.strip()
    ):
        errors.append("Do not mix HTTP tool calls with a Solve decision.")
    errors.extend(
        error
        for call in response.tool_calls
        if (error := probe.validate(config=config, tool_call=call)) is not None
    )
    return errors


def _response_record(response) -> dict:
    """Retain one complete model output in the App-only Solve transcript."""
    return {
        "content": response.content,
        "parsed_json": response.parsed_json,
        "tool_calls": [
            call.model_dump(mode="json")
            for call in response.tool_calls
        ],
        "finish_reason": response.finish_reason,
    }
