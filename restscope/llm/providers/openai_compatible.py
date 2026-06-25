"""OpenAI-compatible chat completions provider adapter."""

from __future__ import annotations

import json
import time
from typing import Any

from restscope.llm.exceptions import InvalidProviderResponseError, ProviderAuthError, ProviderInvokeError
from restscope.llm.providers.base import BaseLLMProvider
from restscope.llm.schemas import LLMMessage, LLMRequest, LLMResponse, ToolCall, ToolSpec


class OpenAICompatibleProvider(BaseLLMProvider):
    """Adapter for providers exposing the OpenAI chat-completions API."""

    name = "openai_compatible"

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str | None = None,
        client: Any | None = None,
    ) -> None:
        if not api_key and client is None:
            raise ProviderAuthError("OpenAI-compatible provider requires an API key")
        self.api_key = api_key
        self.base_url = base_url
        self.client = client or self._build_client(api_key=api_key, base_url=base_url)

    def invoke(self, request: LLMRequest) -> LLMResponse:
        kwargs = self._request_kwargs(request)
        started = time.perf_counter()
        try:
            raw_response = self.client.chat.completions.create(**kwargs)
        except Exception as exc:  # pragma: no cover - SDK-specific branches are provider dependent.
            raise ProviderInvokeError(str(exc)) from exc

        latency_ms = int((time.perf_counter() - started) * 1000)
        return self._normalize_response(request, raw_response, latency_ms)

    def _build_client(self, *, api_key: str, base_url: str | None):
        try:
            from openai import OpenAI
        except ImportError as exc:  # pragma: no cover - covered by dependency metadata.
            raise ProviderInvokeError("The openai package is required for OpenAICompatibleProvider") from exc

        kwargs: dict[str, Any] = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        return OpenAI(**kwargs)

    def _request_kwargs(self, request: LLMRequest) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "model": request.model,
            "messages": [self._message_to_openai(message) for message in request.messages],
            "temperature": request.temperature,
            "max_tokens": request.max_tokens,
            "timeout": request.timeout_seconds,
        }

        response_format = self._response_format(request)
        if response_format is not None:
            kwargs["response_format"] = response_format

        tools = [self._tool_to_openai(tool) for tool in request.tools if tool.kind in {"local_function", "mcp_tool"}]
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = request.tool_choice

        return kwargs

    def _message_to_openai(self, message: LLMMessage) -> dict[str, Any]:
        payload: dict[str, Any] = {"role": message.role, "content": message.content}
        if message.tool_call_id:
            payload["tool_call_id"] = message.tool_call_id
        if message.name:
            payload["name"] = message.name
        return payload

    def _response_format(self, request: LLMRequest) -> dict[str, Any] | None:
        if request.response_format == "text":
            return None
        if request.response_format == "json":
            return {"type": "json_object"}
        if request.response_format == "json_schema":
            if not request.json_schema:
                return {"type": "json_object"}
            return {
                "type": "json_schema",
                "json_schema": {
                    "name": request.json_schema_name or "Response",
                    "schema": request.json_schema,
                    "strict": True,
                },
            }
        return None

    def _tool_to_openai(self, tool: ToolSpec) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.input_schema,
            },
        }

    def _normalize_response(self, request: LLMRequest, raw_response: Any, latency_ms: int) -> LLMResponse:
        try:
            choice = raw_response.choices[0]
            message = choice.message
        except Exception as exc:
            raise InvalidProviderResponseError("OpenAI-compatible response did not include a message") from exc

        content = getattr(message, "content", None)
        parsed_json = self._parse_json_content(content)
        usage = getattr(raw_response, "usage", None)
        return LLMResponse(
            provider=self.name,
            model=request.model,
            content=content,
            parsed_json=parsed_json,
            tool_calls=self._extract_tool_calls(message),
            prompt_tokens=getattr(usage, "prompt_tokens", None),
            completion_tokens=getattr(usage, "completion_tokens", None),
            total_tokens=getattr(usage, "total_tokens", None),
            finish_reason=getattr(choice, "finish_reason", None),
            provider_request_id=getattr(raw_response, "id", None),
            latency_ms=latency_ms,
        )

    def _extract_tool_calls(self, message: Any) -> list[ToolCall]:
        raw_tool_calls = getattr(message, "tool_calls", None) or []
        tool_calls: list[ToolCall] = []
        for raw_call in raw_tool_calls:
            function = getattr(raw_call, "function", None)
            if function is None and isinstance(raw_call, dict):
                function = raw_call.get("function", {})
            name = getattr(function, "name", None) if function is not None else None
            arguments = getattr(function, "arguments", "{}") if function is not None else "{}"
            if isinstance(function, dict):
                name = function.get("name")
                arguments = function.get("arguments", "{}")
            if not name:
                continue
            tool_calls.append(
                ToolCall(
                    id=getattr(raw_call, "id", None) or (raw_call.get("id") if isinstance(raw_call, dict) else ""),
                    name=name,
                    arguments=self._parse_arguments(arguments),
                    provider=self.name,
                    raw=self._raw_tool_call_dict(raw_call),
                )
            )
        return tool_calls

    def _parse_json_content(self, content: str | None) -> Any | None:
        if not content:
            return None
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            return None

    def _parse_arguments(self, arguments: Any) -> dict[str, Any]:
        if isinstance(arguments, dict):
            return arguments
        if not arguments:
            return {}
        try:
            parsed = json.loads(arguments)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}

    def _raw_tool_call_dict(self, raw_call: Any) -> dict[str, Any]:
        if isinstance(raw_call, dict):
            return raw_call
        if hasattr(raw_call, "model_dump"):
            return raw_call.model_dump(mode="json")
        return {"repr": repr(raw_call)}
