"""Compare accepted and candidate batches for one target failure.

This independent THINK boundary sees complete aligned evidence. It does not
compile Patches or send HTTP. A malformed decision gets one correction, and
exhaustion fails closed as ``unknown`` so the coordinator rolls the candidate
back before returning evidence to Failure Solve.
"""

from __future__ import annotations

import json

from restscope.agent.prompt_context import (
    fit_message_context,
    fit_prompt_context,
)
from restscope.llm import (
    LLMClient,
    LLMMessage,
    LLMModelConfig,
    LLMRequest,
    OutputValidator,
)

from .schemas import (
    SmokeEffectDecision,
    SmokeEffectOutcome,
    SmokeEffectRequest,
)


class SmokeEffectAgent:
    """Judge whether one candidate solves its todo without known regression."""

    def __init__(
        self,
        *,
        client: LLMClient,
        model: LLMModelConfig,
        validator: OutputValidator | None = None,
    ) -> None:
        """Store immutable collaborators for independent Effect calls."""
        self.client = client
        self.model = model
        self.validator = validator or OutputValidator()

    def validate(
        self,
        request: SmokeEffectRequest,
        *,
        max_outputs: int = 2,
    ) -> SmokeEffectOutcome:
        """Return a valid Effect decision or fail closed after at most two outputs."""
        if not 1 <= max_outputs <= 2:
            raise ValueError("max_outputs must be between 1 and 2")
        if not self.model.enabled:
            raise RuntimeError("The Operation Smoke Effect model is not configured")

        system = (
            "Independently assess one atomic Operation Smoke Patch. Compare "
            "aligned before and candidate cases using complete requests and "
            "responses. Return resolved_without_regression only when the target "
            "failure is gone and no previously successful case or resolved "
            "failure regressed. Return unresolved when the same target remains, "
            "regression when the Patch caused known behavior to fail, or unknown "
            "when evidence is insufficient. Return JSON with exactly outcome "
            "and reason."
        )
        fitted = fit_prompt_context(
            required=request.model_dump(
                mode="json",
                exclude={"history"},
            ),
            history=request.history,
            model=self.model,
        )
        messages = [
            LLMMessage(role="system", content=system),
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
        errors: list[str] = []
        output_history: list[dict] = []
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
                    tools=[],
                    tool_choice="none",
                    timeout_seconds=self.model.timeout_seconds,
                    reasoning=self.model.reasoning,
                    metadata={"role": "operation_smoke_effect_validation"},
                )
            )
            output_history.append(
                {
                    "content": response.content,
                    "parsed_json": response.parsed_json,
                    "finish_reason": response.finish_reason,
                }
            )
            validation = self.validator.validate(
                response=response,
                output_model=SmokeEffectDecision,
            )
            if validation.valid:
                decision = SmokeEffectDecision.model_validate(
                    validation.validated_object
                )
                return SmokeEffectOutcome(
                    **decision.model_dump(mode="json"),
                    outputs_used=output_number,
                    output_history=list(output_history),
                )
            errors = [
                (
                    f"{issue.location}: {issue.message}"
                    if issue.location
                    else issue.message
                )
                for issue in validation.errors
            ]
            messages.extend(
                (
                    LLMMessage(
                        role="assistant",
                        content=json.dumps(
                            response.parsed_json
                            if response.parsed_json is not None
                            else response.content,
                            ensure_ascii=False,
                            separators=(",", ":"),
                            default=str,
                        ),
                    ),
                    LLMMessage(
                        role="user",
                        content=(
                            "The previous Effect output could not be used:\n"
                            + "\n".join(f"- {error}" for error in errors)
                            + "\nReturn one complete object with exactly outcome "
                            "and reason."
                        ),
                    ),
                )
            )
        return SmokeEffectOutcome(
            outcome="unknown",
            reason=(
                "The Effect output budget was exhausted: "
                + "; ".join(errors)
            ),
            outputs_used=max_outputs,
            output_history=list(output_history),
        )
