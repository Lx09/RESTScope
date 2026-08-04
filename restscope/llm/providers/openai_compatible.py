"""Adapt provider-neutral model requests to OpenAI-compatible SDK clients.

The Adapter receives :class:`LLMRequest` values from ``LLMClient``, translates
them to Chat Completions arguments, and returns normalized :class:`LLMResponse`
values. The SDK owns short retries for one request; after exhaustion this
module exposes a safe provider-unavailable classification to workflows.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from typing import Any

from restscope.llm.exceptions import (
    InvalidProviderResponseError,
    ProviderAuthError,
    ProviderInvokeError,
    ProviderUnavailableError,
)
from restscope.llm.providers.base import BaseLLMProvider
from restscope.llm.schemas import LLMMessage, LLMRequest, LLMResponse, ToolCall, ToolSpec


# The SDK applies these retries only to a model request that has not returned.
# RESTScope deliberately does not retry an Agent turn, tool call, or workflow.
_PROVIDER_RETRY_LIMIT = 3


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
        """Create or accept one OpenAI-compatible SDK client.

        Args:
            api_key: Provider credential used only when building a client.
            base_url: Optional provider endpoint override.
            client: Optional injected compatible client, primarily for tests.

        Raises:
            ProviderAuthError: Neither an API key nor an injected client exists.
        """
        if not api_key and client is None:
            raise ProviderAuthError("OpenAI-compatible provider requires an API key")
        self.api_key = api_key
        self.base_url = base_url
        self.client = client or self._build_client(api_key=api_key, base_url=base_url)

    def invoke(self, request: LLMRequest) -> LLMResponse:
        """Send one provider-neutral request through the configured SDK client.

        Args:
            request: Messages, model, tools, and output controls for one call.

        Returns:
            A provider-neutral response safe for workflow Agents.

        Raises:
            ProviderUnavailableError: SDK retryable failures exhausted the
                configured request retry limit.
            ProviderInvokeError: Another SDK call failure occurred.
        """
        return self._invoke_with_client(request, client=self.client)

    def _invoke_with_client(
        self,
        request: LLMRequest,
        *,
        client: Any,
    ) -> LLMResponse:
        """Invoke one request through an already-selected compatible client.

        Provider adapters with more than one official endpoint can reuse the
        common request translation and response normalization without
        mutating ``self.client``. Keeping client selection at this private seam
        also makes concurrent calls safe.

        Args:
            request: Provider-neutral messages, tools, and output controls.
            client: OpenAI-compatible SDK client for the selected endpoint.

        Returns:
            The normalized provider response.

        Raises:
            ProviderInvokeError: The SDK call failed for any reason.
        """
        kwargs = self._request_kwargs(request)
        started = time.perf_counter()
        try:
            raw_response = client.chat.completions.create(**kwargs)
        except Exception as exc:  # pragma: no cover - SDK-specific branches are provider dependent.
            status_code = _http_status_code(exc)
            if _is_provider_unavailable(exc, status_code=status_code):
                raise ProviderUnavailableError(
                    status_code=status_code,
                    retry_limit=_PROVIDER_RETRY_LIMIT,
                ) from exc
            raise ProviderInvokeError(str(exc)) from exc

        latency_ms = int((time.perf_counter() - started) * 1000)
        return self._normalize_response(request, raw_response, latency_ms)

    def _build_client(self, *, api_key: str, base_url: str | None):
        """Create one SDK client with the bounded request retry policy.

        Args:
            api_key: Credential passed only to the provider SDK.
            base_url: Optional standard or Beta-compatible endpoint.

        Returns:
            An OpenAI SDK client used for synchronous Chat Completions.

        Raises:
            ProviderInvokeError: The required SDK package is unavailable.
        """
        try:
            from openai import OpenAI
        except ImportError as exc:  # pragma: no cover - covered by dependency metadata.
            raise ProviderInvokeError("The openai package is required for OpenAICompatibleProvider") from exc

        kwargs: dict[str, Any] = {
            "api_key": api_key,
            "max_retries": _PROVIDER_RETRY_LIMIT,
        }
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

        tools = [
            self._tool_to_openai(tool)
            for tool in request.tools
            if tool.kind in {"local_function", "mcp_tool"}
        ]
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = request.tool_choice

        return kwargs

    def _message_to_openai(self, message: LLMMessage) -> dict[str, Any]:
        payload: dict[str, Any] = {"role": message.role, "content": message.content}
        if message.tool_calls:
            payload["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {
                        "name": _provider_tool_name(call.name),
                        "arguments": json.dumps(call.arguments),
                    },
                }
                for call in message.tool_calls
            ]
        if message.tool_call_id:
            payload["tool_call_id"] = message.tool_call_id
        if message.name and message.role != "tool":
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
        function = {
            "name": _provider_tool_name(tool.name),
            "description": tool.description,
            "parameters": tool.input_schema,
        }
        if tool.strict:
            function["strict"] = True
        return {
            "type": "function",
            "function": function,
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
            tool_calls=self._extract_tool_calls(message, request=request),
            prompt_tokens=getattr(usage, "prompt_tokens", None),
            completion_tokens=getattr(usage, "completion_tokens", None),
            total_tokens=getattr(usage, "total_tokens", None),
            finish_reason=getattr(choice, "finish_reason", None),
            provider_request_id=getattr(raw_response, "id", None),
            latency_ms=latency_ms,
        )

    def _extract_tool_calls(self, message: Any, *, request: LLMRequest) -> list[ToolCall]:
        """
        Handle extract tool calls as part of provider-independent language-model
        invocation.

        This private helper keeps one transformation or policy decision explicit so the
        surrounding orchestration remains readable.
        """
        raw_tool_calls = getattr(message, "tool_calls", None) or []
        internal_names = {
            _provider_tool_name(tool.name): tool.name
            for tool in request.tools
            if tool.kind in {"local_function", "mcp_tool"}
        }
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
                    name=internal_names.get(name, name),
                    arguments=self._parse_arguments(arguments),
                    provider=self.name,
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

def _provider_tool_name(name: str) -> str:
    """Encode an internal tool name for OpenAI's function-name character set."""

    if re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name):
        return name
    sanitized = re.sub(r"[^A-Za-z0-9_-]", "_", name)
    digest = hashlib.sha256(name.encode("utf-8")).hexdigest()[:8]
    prefix = sanitized[: 64 - len(digest) - 1]
    return f"{prefix}_{digest}"


def _http_status_code(error: Exception) -> int | None:
    """Return an SDK error's usable HTTP status without reading its body.

    Args:
        error: Exception raised after the SDK finishes its own retry policy.

    Returns:
        A positive integer status code, or ``None`` for transport failures and
        exception shapes that do not expose an HTTP response status.
    """

    status_code = getattr(error, "status_code", None)
    if isinstance(status_code, int) and status_code > 0:
        return status_code
    return None


def _is_provider_unavailable(
    error: Exception,
    *,
    status_code: int | None,
) -> bool:
    """Classify retryable SDK failures after its retry budget is exhausted.

    Args:
        error: Original SDK or transport exception.
        status_code: Sanitized HTTP status extracted from ``error``.

    Returns:
        ``True`` only for the HTTP and transport failures that the OpenAI SDK
        itself treats as retryable.
    """

    if status_code in {408, 409, 429}:
        return True
    if status_code is not None and status_code >= 500:
        return True

    # The class-name check recognizes the public OpenAI SDK exception names
    # without requiring fake clients in tests to construct SDK response data.
    exception_name = type(error).__name__
    return isinstance(error, (ConnectionError, TimeoutError)) or exception_name in {
        "APIConnectionError",
        "APITimeoutError",
    }
