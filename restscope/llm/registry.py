"""Provider registry for LLMClient."""

from __future__ import annotations

from restscope.llm.exceptions import UnknownProviderError
from restscope.llm.providers.base import BaseLLMProvider


class LLMProviderRegistry:
    """Mutable registry of provider adapters keyed by provider name."""

    def __init__(self) -> None:
        self._providers: dict[str, BaseLLMProvider] = {}

    def register(self, provider: BaseLLMProvider) -> None:
        self._providers[provider.name] = provider

    def get(self, name: str) -> BaseLLMProvider:
        try:
            return self._providers[name]
        except KeyError as exc:
            raise UnknownProviderError(f"Unknown LLM provider: {name}") from exc

    def list_names(self) -> list[str]:
        return sorted(self._providers)
