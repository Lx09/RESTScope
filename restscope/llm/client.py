"""Provider-neutral synchronous LLM client."""

from __future__ import annotations

from restscope.llm.registry import LLMProviderRegistry
from restscope.llm.schemas import LLMRequest, LLMResponse
from restscope.observability import TracingRuntime


class LLMClient:
    """The single entry point for model calls in RESTScope."""

    def __init__(
        self,
        registry: LLMProviderRegistry,
        *,
        tracing_runtime: TracingRuntime | None = None,
    ) -> None:
        self.registry = registry
        self.tracing_runtime = tracing_runtime or TracingRuntime.disabled()

    def invoke(self, request: LLMRequest) -> LLMResponse:
        provider = self.registry.get(request.provider)
        with self.tracing_runtime.span(
            "LLMClient.invoke",
            kind="LLM",
            input_value={
                "messages": [
                    message.model_dump(mode="json")
                    for message in request.messages
                ]
            },
            attributes=_request_attributes(request),
        ) as span:
            response = provider.invoke(request)
            span.set_output(
                {
                    "content": response.content,
                    "parsed_json": response.parsed_json,
                    "tool_calls": [
                        tool_call.model_dump(mode="json")
                        for tool_call in response.tool_calls
                    ],
                    "finish_reason": response.finish_reason,
                }
            )
            for name, value in (
                ("llm.token_count.prompt", response.prompt_tokens),
                ("llm.token_count.completion", response.completion_tokens),
                ("llm.token_count.total", response.total_tokens),
                ("restscope.llm.latency_ms", response.latency_ms),
            ):
                if value is not None:
                    span.set_attribute(name, value)
            return response


def _request_attributes(request: LLMRequest) -> dict[str, object]:
    attributes: dict[str, object] = {
        "llm.provider": request.provider,
        "llm.model_name": request.model,
        "llm.temperature": request.temperature,
        "llm.max_tokens": request.max_tokens,
        "llm.response_format": request.response_format,
        "llm.reasoning.mode": request.reasoning.mode,
        "llm.tool_choice": request.tool_choice,
    }
    if request.reasoning.effort is not None:
        attributes["llm.reasoning.effort"] = request.reasoning.effort
    if request.tools:
        attributes["llm.tool_names"] = tuple(tool.name for tool in request.tools)
    return attributes
