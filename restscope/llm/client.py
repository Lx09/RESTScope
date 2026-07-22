"""Provider-neutral synchronous LLM client."""

from __future__ import annotations

from restscope.llm.registry import LLMProviderRegistry
from restscope.llm.schemas import LLMRequest, LLMResponse


class LLMClient:
    """The single entry point for model calls in RESTScope."""

    def __init__(self, registry: LLMProviderRegistry) -> None:
        self.registry = registry

    def invoke(self, request: LLMRequest) -> LLMResponse:
        provider = self.registry.get(request.provider)
        return provider.invoke(request)
