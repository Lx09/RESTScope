"""Run one provider-neutral model call for RESTScope Agents.

Agents supply an :class:`LLMRequest`; the client selects the configured
provider Adapter, returns an :class:`LLMResponse`, and records bounded tracing
facts. It sits between workflow Agents and concrete provider SDK Adapters and
does not retry Agent turns, tools, or workflows.
"""

from __future__ import annotations

from restscope.llm.exceptions import ProviderUnavailableError
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
        """Keep the provider registry and optional run-scoped tracing runtime.

        Args:
            registry: Provider-name lookup used for every model request.
            tracing_runtime: Optional exporter for bounded LLM spans; omitted
                tracing becomes a safe no-op.
        """
        self.registry = registry
        self.tracing_runtime = tracing_runtime or TracingRuntime.disabled()

    def invoke(self, request: LLMRequest) -> LLMResponse:
        """Call one configured provider and trace only bounded model facts.

        Args:
            request: Provider, model, messages, tools, and output controls for
                one Agent decision.

        Returns:
            The provider-neutral model response.

        Raises:
            ProviderUnavailableError: The provider's bounded request attempts
                all failed with a retryable HTTP or transport condition.
            LLMError: Another provider-neutral model boundary failed.
        """
        provider = self.registry.get(request.provider)
        unavailable_error: ProviderUnavailableError | None = None
        with self.tracing_runtime.span(
            "LLMClient.invoke",
            kind="LLM",
            attributes=_request_attributes(request),
        ) as span:
            span.set_llm_input_messages(
                [
                    message.model_dump(mode="json")
                    for message in request.messages
                ]
            )
            try:
                response = provider.invoke(request)
            except ProviderUnavailableError as exc:
                # End the span normally after writing stable fields. Raising
                # outside the context keeps its automatic exception recorder
                # from following ``exc.__cause__`` into a provider body.
                span.set_attribute("restscope.llm.error_code", exc.code)
                if exc.status_code is not None:
                    span.set_attribute(
                        "http.response.status_code",
                        exc.status_code,
                    )
                span.set_attribute(
                    "restscope.llm.provider_retry_limit",
                    exc.retry_limit,
                )
                span.mark_error(str(exc))
                unavailable_error = exc
            else:
                trace_tool_calls = [
                    {
                        "id": tool_call.id,
                        "name": tool_call.name,
                        "arguments": tool_call.arguments,
                    }
                    for tool_call in response.tool_calls
                ]
                span.set_llm_output_messages(
                    [
                        {
                            "role": "assistant",
                            "content": response.content,
                            "tool_calls": trace_tool_calls,
                        }
                    ],
                    summary={
                        "parsed_json": response.parsed_json,
                        "tool_calls": [
                            {
                                "id": tool_call.id,
                                "name": tool_call.name,
                            }
                            for tool_call in response.tool_calls
                        ],
                        "finish_reason": response.finish_reason,
                    },
                )
                if response.finish_reason is not None:
                    span.set_attribute(
                        "llm.finish_reason",
                        response.finish_reason,
                    )
                for name, value in (
                    ("llm.token_count.prompt", response.prompt_tokens),
                    (
                        "llm.token_count.completion",
                        response.completion_tokens,
                    ),
                    ("llm.token_count.total", response.total_tokens),
                    ("restscope.llm.latency_ms", response.latency_ms),
                    (
                        "restscope.llm.provider_retry_count",
                        response.metadata.get("provider_retry_count"),
                    ),
                    (
                        "restscope.llm.strict_tool_beta",
                        response.metadata.get("strict_tool_beta"),
                    ),
                ):
                    if value is not None:
                        span.set_attribute(name, value)
                return response

        # The value is assigned only by the provider-unavailable branch above.
        assert unavailable_error is not None
        raise unavailable_error


def _request_attributes(request: LLMRequest) -> dict[str, object]:
    """Project stable model controls and the internal Agent role into a span.

    The role is workflow metadata chosen by RESTScope, not provider response
    content. Recording it lets one shared model configuration remain separable
    by Agent responsibility in Phoenix without copying arbitrary metadata.
    """
    attributes: dict[str, object] = {
        "llm.provider": request.provider,
        "llm.model_name": request.model,
        "llm.temperature": request.temperature,
        "llm.max_tokens": request.max_tokens,
        "llm.response_format": request.response_format,
        "llm.reasoning.mode": request.reasoning.mode,
        "llm.tool_choice": request.tool_choice,
    }
    role = request.metadata.get("role")
    if isinstance(role, str) and role:
        # Do not forward the complete metadata object. Other keys may be
        # workflow-local or untrusted, while this one bounded role label is the
        # explicit observability contract shared by every RESTScope Agent.
        attributes["restscope.llm.role"] = role[:200]
    if request.reasoning.effort is not None:
        attributes["llm.reasoning.effort"] = request.reasoning.effort
    if request.tools:
        attributes["llm.tool_names"] = tuple(tool.name for tool in request.tools)
        attributes["llm.tool_strict"] = all(tool.strict for tool in request.tools)
    return attributes
