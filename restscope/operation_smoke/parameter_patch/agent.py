"""Ask the model for complete Parameter Patch proposals.

The Patch Agent owns one bounded revision conversation for a Solve requirement.
It returns typed proposals and accepts deterministic compile or review feedback;
it does not compile, sample, review, persist, or accept a candidate itself.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any

from restscope.capabilities import (
    AgentToolbox,
    OPENAPI_FIND_OBSERVED_RESPONSE_FIELDS_TOOL_NAME,
    RESOURCE_LIST_IDS_TOOL_NAME,
    RESOURCE_LIST_RESOURCES_TOOL_NAME,
    OpenAPICapability,
    ResourceIdentifierCapability,
    ToolFailure,
    openapi_find_observed_response_fields_tool_spec,
    resource_list_ids_tool_spec,
    resource_list_resources_tool_spec,
)
from restscope.context import AgentContext, CompactTextWriter, ContextLimits
from restscope.llm import (
    LLMClient,
    LLMModelConfig,
    LLMReasoningConfig,
    LLMRequest,
    LLMResponse,
    OutputValidator,
    ToolResult,
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
        openapi_capability: OpenAPICapability | None = None,
        resource_capability: ResourceIdentifierCapability | None = None,
        validator: OutputValidator | None = None,
        tracing_runtime: TracingRuntime | None = None,
    ) -> None:
        """Create an isolated proposal session from a bounded domain prompt.

        Args:
            client: Provider-neutral model client.
            model: FAST model selected for Patch proposals.
            prompt: Initial Solve requirement and current Generator evidence.
            openapi_capability: Current-IR observed response field lookup.
            resource_capability: Current Resource Identifier Catalog lookup.
            validator: Optional structured-output validator used by tests.
            tracing_runtime: Trace sink; sensitive prompt and reasoning stay out.
        """
        self.client = client
        self.model = model
        self.validator = validator or OutputValidator()
        self.tracing_runtime = tracing_runtime or TracingRuntime.disabled()
        self.toolbox = _build_toolbox(
            openapi_capability=openapi_capability,
            resource_capability=resource_capability,
            tracing_runtime=self.tracing_runtime,
        )
        # These sets contain only successful tool evidence from this short-lived
        # proposal conversation. Compilation still re-reads authoritative state.
        self.listed_resources: set[str] = set()
        self.queried_resources: dict[str, tuple[frozenset[str], int]] = {}
        self.queried_response_fields: set[tuple[str, str, str, str]] = set()
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
            if response.tool_calls:
                submission = None
                errors = self._tool_errors(response)
                transport = "tool_calls"
            else:
                submission, errors = self._parse(response)
                transport = "json_schema"
            span.set_output(
                {
                    "valid": (
                        submission is not None
                        or (bool(response.tool_calls) and not errors)
                    ),
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

    def execute_tools(self, attempt: ParameterPatchAttempt) -> list[ToolResult]:
        """Execute one already-validated read-only lookup group.

        Args:
            attempt: The model output whose tool calls passed protocol checks.

        Returns:
            Sanitized results in provider call order. Successful results update
            only this Agent session's discovery proof; Catalogs remain read-only.

        Raises:
            ProviderUnavailableError: Propagated unchanged by ``AgentToolbox``.
            ValueError: The caller supplied a non-tool or invalid attempt.
        """
        if not attempt.response.tool_calls or attempt.errors:
            raise ValueError("Only a valid Patch lookup output may be executed")
        results = self.toolbox.execute_many(attempt.response.tool_calls)
        self.context.append_assistant(attempt.response)
        for result in results:
            self._remember_tool_result(result)
            self.context.append_tool_result(
                result.name,
                result.tool_call_id,
                _tool_result_text(result),
            )
        return results

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
                tools=self.toolbox.specs(),
                tool_choice="auto",
            )
        )

    def _tool_errors(self, response: LLMResponse) -> list[str]:
        """Reject malformed or dependency-breaking lookup groups before execution."""
        errors: list[str] = []
        if response.parsed_json is not None or (
            response.content is not None and response.content.strip()
        ):
            errors.append("Do not mix Patch lookup calls with a final proposal.")
        call_ids = [call.id for call in response.tool_calls]
        if len(call_ids) != len(set(call_ids)):
            errors.append("Every Patch lookup call must have a unique call id.")
        for call in response.tool_calls:
            if call.name == RESOURCE_LIST_IDS_TOOL_NAME:
                resource = call.arguments.get("resource")
                if resource not in self.listed_resources:
                    errors.append(
                        "Call resource.list_resources first, then pass one "
                        "returned canonical name to resource.list_ids."
                    )
        return errors

    def _remember_tool_result(self, result: ToolResult) -> None:
        """Retain only successful, bounded source identities for compilation."""
        if result.status != "succeeded" or not isinstance(result.structured, dict):
            return
        structured = result.structured
        if result.name == RESOURCE_LIST_RESOURCES_TOOL_NAME:
            for item in structured.get("resources", []):
                if isinstance(item, dict) and isinstance(item.get("name"), str):
                    self.listed_resources.add(item["name"])
            return
        if result.name == RESOURCE_LIST_IDS_TOOL_NAME:
            canonical = structured.get("canonical_resource")
            identifiers = structured.get("ids")
            if (
                structured.get("status") == "found"
                and isinstance(canonical, str)
                and canonical in self.listed_resources
                and isinstance(identifiers, list)
                and identifiers
            ):
                value_types = frozenset(
                    str(item.get("value_type"))
                    for item in identifiers
                    if isinstance(item, dict) and item.get("value_type")
                )
                self.queried_resources[canonical] = (
                    value_types,
                    int(structured.get("total") or len(identifiers)),
                )
            return
        if result.name != OPENAPI_FIND_OBSERVED_RESPONSE_FIELDS_TOOL_NAME:
            return
        for response in structured.get("responses", []):
            if not isinstance(response, dict):
                continue
            prefix = (
                response.get("operation_key"),
                response.get("matched_status_code"),
                response.get("media_type"),
            )
            if not all(isinstance(item, str) for item in prefix):
                continue
            for field in response.get("fields", []):
                if isinstance(field, dict) and isinstance(field.get("field"), str):
                    self.queried_response_fields.add((*prefix, field["field"]))

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


def _build_toolbox(
    *,
    openapi_capability: OpenAPICapability | None,
    resource_capability: ResourceIdentifierCapability | None,
    tracing_runtime: TracingRuntime,
) -> AgentToolbox:
    """Build the Patch Agent's exact three-tool, read-only permission set."""
    toolbox = AgentToolbox(tracing_runtime=tracing_runtime)
    toolbox.register(
        spec=resource_list_resources_tool_spec(),
        execute=(
            resource_capability.list_resources
            if resource_capability is not None
            else _unavailable_lookup
        ),
    )
    toolbox.register(
        spec=resource_list_ids_tool_spec(),
        execute=(
            resource_capability.list_ids
            if resource_capability is not None
            else _unavailable_lookup
        ),
    )
    toolbox.register(
        spec=openapi_find_observed_response_fields_tool_spec(),
        execute=(
            openapi_capability.find_observed_response_fields
            if openapi_capability is not None
            else _unavailable_lookup
        ),
    )
    return toolbox


def _unavailable_lookup(**_arguments: Any) -> dict[str, Any]:
    """Return a safe tool failure when a focused test omits App capabilities."""
    raise ToolFailure(
        code="patch_lookup_unavailable",
        message="This Patch lookup capability is unavailable in the current runtime.",
    )


def _tool_result_text(result: ToolResult) -> str:
    """Render lookup output as bounded untrusted data for the next model turn."""
    writer = CompactTextWriter(max_value_chars=1_200)
    writer.section("PATCH LOOKUP RESULT", untrusted=True)
    writer.record(
        result.name,
        status=result.status,
        error=result.error,
    )
    if result.structured is not None:
        writer.json_block("lookup result", result.structured)
    return writer.render(max_chars=12_000).text


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
