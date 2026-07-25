"""Provider-neutral synchronous LLM client."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

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
            input_value=_trace_payload(request),
            attributes={
                "llm.provider": request.provider,
                "llm.model_name": request.model,
            },
        ) as span:
            response = provider.invoke(request)
            span.set_output(_trace_payload(response))
            for name, value in (
                ("llm.token_count.prompt", response.prompt_tokens),
                ("llm.token_count.completion", response.completion_tokens),
                ("llm.token_count.total", response.total_tokens),
                ("restscope.llm.latency_ms", response.latency_ms),
            ):
                if value is not None:
                    span.set_attribute(name, value)
            return response


def _trace_payload(value: Any) -> Any:
    """Remove provider-private reasoning state from an LLM trace projection."""

    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    if isinstance(value, Mapping):
        return {
            str(key): _trace_payload(item)
            for key, item in value.items()
            if key not in {"provider_context", "reasoning_content"}
        }
    if isinstance(value, Sequence) and not isinstance(
        value,
        str | bytes | bytearray,
    ):
        return [_trace_payload(item) for item in value]
    return value
