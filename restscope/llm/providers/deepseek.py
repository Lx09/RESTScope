"""Official DeepSeek Chat Completions provider adapter."""

from __future__ import annotations

import json
from typing import Any

from restscope.llm.exceptions import ProviderInvokeError
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

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str | None = None,
        client: Any | None = None,
        default_reasoning: LLMReasoningConfig | None = None,
    ) -> None:
        self.default_reasoning = default_reasoning or LLMReasoningConfig(mode="enabled")
        super().__init__(
            api_key=api_key,
            base_url=base_url or self.default_base_url,
            client=client,
        )

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
