"""Configured construction for the OpenAPI Retrieval Agent."""

from __future__ import annotations

from restscope.llm import LLMClient, ModelSelector, build_llm_client
from restscope.restscope_config import RESTScopeConfig

from .agent import OpenAPIRetrievalAgent


def build_openapi_retrieval_agent(
    config: RESTScopeConfig,
    *,
    llm_client: LLMClient | None = None,
) -> OpenAPIRetrievalAgent:
    """Build the Agent with the configured thinking model."""

    client = llm_client or build_llm_client(config.llm)
    model = ModelSelector.from_config(config.llm).select("openapi_retrieval")
    return OpenAPIRetrievalAgent(client=client, model=model)
