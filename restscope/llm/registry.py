"""Provider registry for LLMClient."""

from __future__ import annotations

from restscope.llm.exceptions import UnknownProviderError
from restscope.llm.providers.base import BaseLLMProvider


class LLMProviderRegistry:
    """Mutable registry of provider adapters keyed by provider name."""

    def __init__(self) -> None:
        self._providers: dict[str, BaseLLMProvider] = {}

    def register(self, provider: BaseLLMProvider) -> None:
        """Register one provider factory under a unique provider name."""
        self._providers[provider.name] = provider

    def get(self, name: str) -> BaseLLMProvider:
        """Return the registered provider factory or raise for an unknown name."""
        try:
            return self._providers[name]
        except KeyError as exc:
            raise UnknownProviderError(f"Unknown LLM provider: {name}") from exc

    def list_names(self) -> list[str]:
        """List registered provider names in deterministic order."""
        return sorted(self._providers)
