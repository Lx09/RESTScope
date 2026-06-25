"""Provider interface for model-specific adapters."""

from __future__ import annotations

from abc import ABC, abstractmethod

from restscope.llm.schemas import LLMRequest, LLMResponse


class BaseLLMProvider(ABC):
    """A synchronous adapter from RESTScope requests to one provider API."""

    name: str

    @abstractmethod
    def invoke(self, request: LLMRequest) -> LLMResponse:
        """Invoke the provider and return a normalized response."""

    async def ainvoke(self, request: LLMRequest) -> LLMResponse:
        """Async providers are intentionally post-MVP."""

        raise NotImplementedError("Async LLM providers are not implemented in the MVP")
