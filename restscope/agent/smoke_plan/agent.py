"""Identify failure todos from one complete Operation Smoke batch.

This Agent owns only semantic failure management. It does not inspect the
OpenAPI schema, diagnose a parameter, or propose a patch. The caller supplies
the complete failed cases plus App-lifetime history, and receives an ordered
list whose temporary references have already been expanded.
"""

from __future__ import annotations

import json

from restscope.llm import (
    LLMClient,
    LLMMessage,
    LLMModelConfig,
    LLMRequest,
    OutputValidator,
)
from restscope.agent.prompt_context import (
    fit_message_context,
    fit_prompt_context,
)

from .schemas import (
    FailureTodo,
    SmokePlanDecision,
    SmokePlanRequest,
    SmokeRoundPlan,
)


class SmokePlanAgent:
    """Ask a THINK model to identify unique failure work for one batch."""

    def __init__(
        self,
        *,
        client: LLMClient,
        model: LLMModelConfig,
        validator: OutputValidator | None = None,
    ) -> None:
        """Store immutable model collaborators used by stateless Plan calls."""
        self.client = client
        self.model = model
        self.validator = validator or OutputValidator()

    def plan(
        self,
        request: SmokePlanRequest,
        *,
        max_outputs: int = 50,
    ) -> SmokeRoundPlan:
        """Build one round plan, counting every model output against its budget.

        ``request`` contains temporary case codes only for this boundary.
        Invalid DTOs, unknown codes, missing failed cases, and duplicate todo
        IDs are returned to the same short-lived Plan conversation. If the
        budget ends first, the caller receives a structured exhaustion result
        rather than an exception.
        """
        if not 1 <= max_outputs <= 50:
            raise ValueError("max_outputs must be between 1 and 50")
        if not self.model.enabled:
            raise RuntimeError("The Operation Smoke Plan model is not configured")

        system = (
            "Manage one Operation Smoke batch. Identify semantically unique "
            "failures, associate every failed case code with at least one "
            "ordered todo, and do not diagnose root causes, parameters, "
            "dependencies, or patches. Use action=no_new_failure_work only "
            "when the supplied App-lifetime history shows there is no new "
            "failure work. Return JSON with exactly action, todos, and reason. "
            "Each todo has exactly todo_id, failure, and case_codes."
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
        last_errors: list[str] = []
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
                    metadata={"role": "operation_smoke_plan"},
                )
            )
            validation = self.validator.validate(
                response=response,
                output_model=SmokePlanDecision,
            )
            decision = (
                SmokePlanDecision.model_validate(validation.validated_object)
                if validation.valid
                else None
            )
            errors = [
                (
                    f"{issue.location}: {issue.message}"
                    if issue.location
                    else issue.message
                )
                for issue in validation.errors
            ]
            if decision is not None:
                errors.extend(_semantic_errors(decision, request=request))
            if not errors and decision is not None:
                if decision.action == "no_new_failure_work":
                    return SmokeRoundPlan(
                        status="no_new_failure_work",
                        reason=decision.reason,
                        outputs_used=output_number,
                    )
                return SmokeRoundPlan(
                    status="planned",
                    todos=[
                        FailureTodo(
                            todo_id=todo.todo_id,
                            failure=todo.failure,
                            cases=[
                                request.coded_cases[code]
                                for code in todo.case_codes
                            ],
                        )
                        for todo in decision.todos
                    ],
                    reason=decision.reason,
                    outputs_used=output_number,
                )

            last_errors = errors or ["The Plan output could not be used."]
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
                            "Correct the complete Plan JSON:\n"
                            + "\n".join(f"- {error}" for error in last_errors)
                            + "\nDo not diagnose causes or propose patches."
                        ),
                    ),
                )
            )
        return SmokeRoundPlan(
            status="plan_budget_exhausted",
            reason="; ".join(last_errors),
            outputs_used=max_outputs,
        )


def _semantic_errors(
    decision: SmokePlanDecision,
    *,
    request: SmokePlanRequest,
) -> list[str]:
    """Validate only structural references, leaving semantic uniqueness to LLM."""
    if decision.action != "process":
        return []
    errors: list[str] = []
    todo_ids = [todo.todo_id for todo in decision.todos]
    if len(todo_ids) != len(set(todo_ids)):
        errors.append("todo_id values must be unique.")
    supplied = set(request.coded_cases)
    referenced: set[str] = set()
    for todo in decision.todos:
        for code in todo.case_codes:
            if code not in supplied:
                errors.append(f"{code} was not supplied as a case code.")
            else:
                referenced.add(code)
    for code in request.failed_case_codes:
        if code not in referenced:
            errors.append(f"{code} is a failed case and must be managed.")
    return errors
