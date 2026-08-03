"""Official DeepSeek Chat Completions provider adapter."""

from __future__ import annotations

import json
from typing import Any

from restscope.llm.exceptions import (
    ProviderInvokeError,
    StrictToolUnavailableError,
)
from restscope.llm.providers.openai_compatible import OpenAICompatibleProvider
from restscope.llm.schemas import LLMMessage, LLMReasoningConfig, LLMRequest, LLMResponse


class DeepSeekCompatibilityError(ProviderInvokeError):
    """A request cannot be represented by the supported DeepSeek API contract."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


class DeepSeekProvider(OpenAICompatibleProvider):
    """Adapt provider-neutral LLM requests to the official DeepSeek API."""

    name = "deepseek"
    default_base_url = "https://api.deepseek.com"
    beta_base_url = "https://api.deepseek.com/beta"

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str | None = None,
        client: Any | None = None,
        beta_client: Any | None = None,
        default_reasoning: LLMReasoningConfig | None = None,
    ) -> None:
        """Create standard and lazily-built Beta endpoint adapters.

        Args:
            api_key: Credential shared by the two official DeepSeek endpoints.
            base_url: Standard official endpoint, the official ``/beta`` URL,
                or a custom compatibility gateway. Custom gateways remain
                usable for ordinary calls but are not assumed to expose Beta.
            client: Optional standard client used by tests or custom wiring.
            beta_client: Optional Beta client used by strict-call tests.
            default_reasoning: Reasoning controls inherited by requests that
                leave their mode at ``default``.
        """
        self.default_reasoning = default_reasoning or LLMReasoningConfig(mode="enabled")
        configured_url = (base_url or self.default_base_url).rstrip("/")
        if configured_url == self.beta_base_url:
            standard_url = self.default_base_url
            strict_url: str | None = self.beta_base_url
        elif configured_url == self.default_base_url:
            standard_url = self.default_base_url
            strict_url = self.beta_base_url
        else:
            standard_url = configured_url
            strict_url = None
        self._strict_base_url = strict_url
        self._beta_client = beta_client
        self._beta_api_key = api_key
        super().__init__(
            api_key=api_key,
            base_url=standard_url,
            client=client,
        )

    def invoke(self, request: LLMRequest) -> LLMResponse:
        """Route strict tools to Beta and retry incomplete reasoning once.

        A DeepSeek strict request must contain only strict functions. The
        official Beta endpoint validates those schemas before generation.
        Custom gateways are not assumed to implement that endpoint; callers
        receive :class:`StrictToolUnavailableError` and may use an explicitly
        owned fallback.

        DeepSeek requires ``reasoning_content`` from a tool-calling assistant
        message to be replayed verbatim in every later request.  The field
        therefore cannot be invented or replaced with an empty value.  A retry
        is safe here because normalization fails before RESTScope returns the
        tool call to an Agent, so no tool or target-API side effect has run.

        Any second incomplete response, or any other provider error, still
        propagates to the caller instead of creating an unbounded retry loop.
        """
        strict_tools = [tool for tool in request.tools if tool.strict]
        if strict_tools and len(strict_tools) != len(request.tools):
            raise DeepSeekCompatibilityError(
                "deepseek_mixed_strict_tools",
                "DeepSeek strict requests require every supplied function to be strict",
            )
        if strict_tools:
            if self._strict_base_url is None:
                raise StrictToolUnavailableError(
                    "deepseek_strict_endpoint_unavailable",
                    "the configured DeepSeek gateway has no declared Beta endpoint",
                )
            try:
                response = self._invoke_with_reasoning_retry(
                    request,
                    client=self._get_beta_client(),
                )
            except ProviderInvokeError as exc:
                reason = _strict_unavailable_reason(exc.__cause__)
                if reason is None:
                    raise
                raise StrictToolUnavailableError(
                    reason,
                    "the DeepSeek Beta strict request was unavailable",
                ) from exc
            return response.model_copy(
                update={
                    "metadata": {
                        **response.metadata,
                        "strict_tool_beta": True,
                    }
                }
            )
        return self._invoke_with_reasoning_retry(request, client=self.client)

    def _invoke_with_reasoning_retry(
        self,
        request: LLMRequest,
        *,
        client: Any,
    ) -> LLMResponse:
        """Invoke through one endpoint and retry a missing reasoning field.

        Args:
            request: Provider-neutral request already assigned to an endpoint.
            client: Standard or Beta OpenAI-compatible SDK client.

        Returns:
            A normalized response with complete tool-call continuation data.
        """
        try:
            return self._invoke_with_client(request, client=client)
        except DeepSeekCompatibilityError as exc:
            if exc.code != "deepseek_reasoning_content_missing":
                raise
            # A malformed request history is deterministic and cannot improve
            # on retry. Only retry when the history was complete, which means
            # the provider's newly returned tool response omitted the field.
            if any(
                message.role == "assistant"
                and message.tool_calls
                and any(
                    not call.provider_context.get("reasoning_content")
                    for call in message.tool_calls
                )
                for message in request.messages
            ):
                raise

        response = self._invoke_with_client(request, client=client)
        return response.model_copy(
            update={
                "metadata": {
                    **response.metadata,
                    "provider_retry_count": 1,
                }
            }
        )

    def _get_beta_client(self) -> Any:
        """Build the official Beta client only when a strict call needs it."""
        if self._beta_client is None:
            assert self._strict_base_url is not None
            self._beta_client = self._build_client(
                api_key=self._beta_api_key,
                base_url=self._strict_base_url,
            )
        return self._beta_client

    def _request_kwargs(self, request: LLMRequest) -> dict[str, Any]:
        """
        Handle request kwargs as part of provider-independent language-model invocation.

        This private helper keeps one transformation or policy decision explicit so the
        surrounding orchestration remains readable.
        """
        reasoning = self._effective_reasoning(request)
        self._validate_reasoning_history(request, reasoning=reasoning)
        thinking_enabled = reasoning.mode != "disabled"
        if thinking_enabled and request.tool_choice not in {"auto", "none"}:
            raise DeepSeekCompatibilityError(
                "deepseek_tool_choice_unsupported",
                "DeepSeek thinking mode does not support forced tool choice",
            )
        kwargs = super()._request_kwargs(request)
        mode = reasoning.mode
        if mode != "default":
            kwargs["extra_body"] = {"thinking": {"type": mode}}
        if mode != "disabled" and reasoning.effort is not None:
            kwargs["reasoning_effort"] = reasoning.effort
        if thinking_enabled:
            kwargs.pop("temperature", None)

        if thinking_enabled and "tools" in kwargs:
            if request.tool_choice == "auto":
                kwargs.pop("tool_choice", None)
            elif request.tool_choice == "none":
                kwargs.pop("tools", None)
                kwargs.pop("tool_choice", None)

        if request.response_format in {"json", "json_schema"}:
            kwargs["response_format"] = {"type": "json_object"}
            kwargs["messages"] = self._with_json_instruction(
                kwargs["messages"],
                request=request,
            )
        return kwargs

    def _message_to_openai(self, message: LLMMessage) -> dict[str, Any]:
        payload = super()._message_to_openai(message)
        if message.role == "assistant" and message.tool_calls:
            reasoning_values = {
                value
                for call in message.tool_calls
                if (value := call.provider_context.get("reasoning_content"))
            }
            if len(reasoning_values) == 1:
                payload["reasoning_content"] = reasoning_values.pop()
        return payload

    def _normalize_response(
        self,
        request: LLMRequest,
        raw_response: Any,
        latency_ms: int,
    ) -> LLMResponse:
        """
        Normalize response for provider-independent language-model invocation.

        This private helper keeps one transformation or policy decision explicit so the
        surrounding orchestration remains readable.
        """
        response = super()._normalize_response(request, raw_response, latency_ms)
        if not response.tool_calls:
            return response

        message = raw_response.choices[0].message
        reasoning_content = getattr(message, "reasoning_content", None)
        if self._effective_reasoning(request).mode != "disabled" and not reasoning_content:
            raise DeepSeekCompatibilityError(
                "deepseek_reasoning_content_missing",
                "DeepSeek thinking tool-call response did not include reasoning_content",
            )
        if not reasoning_content:
            return response

        return response.model_copy(
            update={
                "tool_calls": [
                    call.model_copy(
                        update={
                            "provider_context": {
                                **call.provider_context,
                                "reasoning_content": reasoning_content,
                            }
                        }
                    )
                    for call in response.tool_calls
                ]
            }
        )

    def _validate_reasoning_history(
        self,
        request: LLMRequest,
        *,
        reasoning: LLMReasoningConfig,
    ) -> None:
        if reasoning.mode == "disabled":
            return
        for message in request.messages:
            if message.role != "assistant" or not message.tool_calls:
                continue
            reasoning_values = [
                call.provider_context.get("reasoning_content")
                for call in message.tool_calls
            ]
            if any(not value for value in reasoning_values) or len(set(reasoning_values)) != 1:
                raise DeepSeekCompatibilityError(
                    "deepseek_reasoning_content_missing",
                    "DeepSeek thinking tool-call history must preserve one reasoning_content value",
                )

    def _effective_reasoning(self, request: LLMRequest) -> LLMReasoningConfig:
        if request.reasoning.mode == "default":
            return self.default_reasoning
        return request.reasoning

    def _with_json_instruction(
        self,
        messages: list[dict[str, Any]],
        *,
        request: LLMRequest,
    ) -> list[dict[str, Any]]:
        converted = [dict(message) for message in messages]
        if request.response_format == "json" and any(
            "json" in str(message.get("content", "")).casefold()
            for message in converted
        ):
            return converted

        instruction = "Return only valid JSON."
        if request.response_format == "json_schema" and request.json_schema:
            schema = json.dumps(request.json_schema, ensure_ascii=False, separators=(",", ":"))
            instruction = f"{instruction} The JSON value must satisfy this schema: {schema}"

        for message in converted:
            if message["role"] == "system":
                message["content"] = f"{message['content']}\n\n{instruction}"
                return converted
        converted.insert(0, {"role": "system", "content": instruction})
        return converted


def _strict_unavailable_reason(exc: BaseException | None) -> str | None:
    """Classify failures for which a caller-owned legacy fallback is safe.

    Authentication, permission, and rate-limit failures intentionally return
    ``None``. Retrying those requests through the standard endpoint would hide
    the real operational problem rather than provide compatibility.

    Args:
        exc: Original SDK exception wrapped by the compatible Adapter.

    Returns:
        A stable reason code, or ``None`` when the error must propagate.
    """
    if exc is None:
        return None
    status_code = getattr(exc, "status_code", None)
    if status_code in {401, 403, 429}:
        return None
    if status_code in {400, 404, 405, 422}:
        return "deepseek_strict_schema_or_route_rejected"
    if isinstance(status_code, int) and status_code >= 500:
        return "deepseek_strict_endpoint_unavailable"

    # The OpenAI SDK exposes connection and timeout classes, but keeping this
    # check name-based also supports test doubles without importing SDK-private
    # exception constructors.
    exception_name = type(exc).__name__
    if isinstance(exc, (ConnectionError, TimeoutError)) or exception_name in {
        "APIConnectionError",
        "APITimeoutError",
    }:
        return "deepseek_strict_endpoint_unavailable"
    return None
