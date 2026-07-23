"""Configured construction for the OpenAPI Retrieval Agent."""

from __future__ import annotations

from restscope.llm import LLMClient, ModelSelector, build_llm_client
from restscope.observability import TracingRuntime
from restscope.restscope_config import RESTScopeConfig

from .agent import OpenAPIRetrievalAgent


def build_openapi_retrieval_agent(
    config: RESTScopeConfig,
    *,
    llm_client: LLMClient | None = None,
    tracing_runtime: TracingRuntime | None = None,
) -> OpenAPIRetrievalAgent:
    """Build the Agent with the configured thinking model."""

    runtime = tracing_runtime
    if runtime is None and llm_client is not None:
        runtime = getattr(llm_client, "tracing_runtime", None)
    runtime = runtime or TracingRuntime.disabled()
    runtime.redactor.register_secrets(
        (
            config.llm.thinking.api_key,
            config.llm.fast.api_key,
        )
    )
    client = llm_client or build_llm_client(
        config.llm,
        tracing_runtime=runtime,
    )
    if hasattr(client, "tracing_runtime"):
        client.tracing_runtime = runtime
    model = ModelSelector.from_config(config.llm).select("openapi_retrieval")
    return OpenAPIRetrievalAgent(
        client=client,
        model=model,
        tracing_runtime=runtime,
    )
