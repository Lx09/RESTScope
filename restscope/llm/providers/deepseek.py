"""Route provider-neutral requests across official DeepSeek endpoints.

Ordinary requests use the standard URL and strict function requests first use
the Beta URL. Both return normalized LLM responses to ``LLMClient``; a bounded
Beta capacity failure may fall back to the standard URL before workflow code
or an Agent receives any tool call.
"""

from __future__ import annotations

import json

from restscope.llm.exceptions import (
    ProviderInvokeError,
    ProviderUnavailableError,
    StrictToolUnavailableError,
)
from restscope.llm.providers.openai_compatible import OpenAICompatibleProvider
from restscope.llm.schemas import LLMMessage, LLMReasoningConfig, LLMRequest, LLMResponse


# DeepSeek occasionally returns a thinking-mode tool call without the
# ``reasoning_content`` that its own continuation contract requires. Three
# total requests absorb a short transient streak without creating an
# unbounded provider loop. No tool is visible to workflow code during retries.
_REASONING_RESPONSE_ATTEMPTS = 3


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
        client: object | None = None,
        beta_client: object | None = None,
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
        """Route strict tools to Beta and retry incomplete reasoning briefly.

        A DeepSeek strict request must contain only strict functions. The
        official Beta endpoint validates those schemas before generation. If
        that endpoint remains unavailable after SDK retries, the same request
        gets one attempt through the standard official URL before any tool
        call can be returned to an Agent. Custom gateways are not assumed to
        expose Beta and still report :class:`StrictToolUnavailableError`.

        DeepSeek requires ``reasoning_content`` from a tool-calling assistant
        message to be replayed verbatim in every later request.  The field
        therefore cannot be invented or replaced with an empty value.  A retry
        is safe here because normalization fails before RESTScope returns the
        tool call to an Agent, so no tool or target-API side effect has run.

        A short bounded series of incomplete responses is retried. Exhausting
        that bound, or any error from the standard fallback, still propagates
        instead of creating an unbounded loop.
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
            used_beta = True
            try:
                response = self._invoke_with_reasoning_retry(
                    request,
                    client=self._get_beta_client(),
                )
            except ProviderUnavailableError:
                # Beta and standard are two endpoints for the same configured
                # provider/model. This one fallback remains safe because the
                # failed model request has returned no Agent-visible tool call.
                response = self._invoke_with_reasoning_retry(
                    request,
                    client=self.client,
                )
                used_beta = False
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
                        "strict_tool_beta": used_beta,
                    }
                }
            )
        return self._invoke_with_reasoning_retry(request, client=self.client)

    def _invoke_with_reasoning_retry(
        self,
        request: LLMRequest,
        *,
        client: object,
    ) -> LLMResponse:
        """Invoke through one endpoint and retry a missing reasoning field.

        Args:
            request: Provider-neutral request already assigned to an endpoint.
            client: Standard or Beta OpenAI-compatible SDK client.

        Returns:
            A normalized response with complete tool-call continuation data.
        """
        # Invalid continuation history is deterministic. Validate it before
        # the retry loop so a bad local request never reaches the provider.
        self._validate_reasoning_history(
            request,
            reasoning=self._effective_reasoning(request),
        )
        for attempt_index in range(_REASONING_RESPONSE_ATTEMPTS):
            try:
                response = self._invoke_with_client(request, client=client)
            except DeepSeekCompatibilityError as exc:
                if (
                    exc.code != "deepseek_reasoning_content_missing"
                    or attempt_index == _REASONING_RESPONSE_ATTEMPTS - 1
                ):
                    raise
                # Normalization rejected the new response before its tool call
                # became Agent-visible, so another provider request cannot
                # duplicate a RESTScope tool or target-API side effect.
                continue

            if attempt_index == 0:
                return response
            return response.model_copy(
                update={
                    "metadata": {
                        **response.metadata,
                        "provider_retry_count": attempt_index,
                    }
                }
            )

        # The loop either returns a complete response or raises on its final
        # incomplete one. This guard documents that invariant for type checkers.
        raise AssertionError("DeepSeek reasoning retry loop ended unexpectedly")

    def _get_beta_client(self) -> object:
        """Build the official Beta client only when a strict call needs it."""
        if self._beta_client is None:
            assert self._strict_base_url is not None
            self._beta_client = self._build_client(
                api_key=self._beta_api_key,
                base_url=self._strict_base_url,
            )
        return self._beta_client

    def _request_kwargs(self, request: LLMRequest) -> dict[str, object]:
        """Build DeepSeek request options while omitting unsupported or unset fields."""
        reasoning = self._effective_reasoning(request)
        self._validate_reasoning_history(request, reasoning=reasoning)
        thinking_enabled = reasoning.mode != "disabled"
        if thinking_enabled and request.tool_choice not in {"auto", "none"}:
            raise DeepSeekCompatibilityError(
                "deepseek_tool_choice_unsupported",
                "DeepSeek thinking mode does not support forced tool choice",
            )
        kwargs = super()._request_kwargs(_merge_developer_messages(request))
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

    def _message_to_openai(self, message: LLMMessage) -> dict[str, object]:
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
        raw_response: object,
        latency_ms: int,
    ) -> LLMResponse:
        """Translate one DeepSeek response into the shared LLMResponse contract, preserving usage and tool calls."""
        response = super()._normalize_response(request, raw_response, latency_ms)
        message = raw_response.choices[0].message
        reasoning_content = getattr(message, "reasoning_content", None)
        response = response.model_copy(
            update={"reasoning_content": reasoning_content or None}
        )
        if not response.tool_calls:
            return response

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
        messages: list[dict[str, object]],
        *,
        request: LLMRequest,
    ) -> list[dict[str, object]]:
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


def _merge_developer_messages(request: LLMRequest) -> LLMRequest:
    """Represent developer guidance through DeepSeek's supported role set.

    OpenAI-compatible requests can transmit ``developer`` directly. DeepSeek's
    chat contract currently exposes system, user, assistant, and tool roles, so
    each developer message is folded into the nearest preceding system message.
    The transformation preserves message order and leaves requests without a
    developer role byte-for-byte equivalent at the Pydantic value boundary.
    """
    if not any(message.role == "developer" for message in request.messages):
        return request

    messages: list[LLMMessage] = []
    for message in request.messages:
        if message.role != "developer":
            messages.append(message)
            continue
        if messages and messages[-1].role == "system":
            previous = messages[-1]
            messages[-1] = previous.model_copy(
                update={"content": f"{previous.content}\n\n{message.content}"}
            )
        else:
            messages.append(LLMMessage(role="system", content=message.content))
    return request.model_copy(update={"messages": messages})


def _strict_unavailable_reason(exc: BaseException | None) -> str | None:
    """Classify failures for which a caller-owned legacy fallback is safe.

    Authentication and permission failures intentionally return ``None``.
    Retryable capacity failures normally take the standard-URL fallback before
    reaching this helper; the 429 check remains defensive so rate limits can
    never be mislabeled as schema compatibility failures.

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
