"""Provider registry for LLMClient."""

from __future__ import annotations

from restscope.llm.exceptions import UnknownProviderError
from restscope.llm.providers.base import BaseLLMProvider


class LLMProviderRegistry:
    """Mutable registry of provider adapters keyed by provider name."""

    def __init__(self) -> None:
        self._providers: dict[str, BaseLLMProvider] = {}

    def register(self, provider: BaseLLMProvider) -> None:
        """
        Handle register as part of provider-independent language-model invocation.

        The class owns any required collaborators or state; arguments supply only the
        data needed for this call.
        """
        self._providers[provider.name] = provider

    def get(self, name: str) -> BaseLLMProvider:
        """
        Handle get as part of provider-independent language-model invocation.

        The class owns any required collaborators or state; arguments supply only the
        data needed for this call.
        """
        try:
            return self._providers[name]
        except KeyError as exc:
            raise UnknownProviderError(f"Unknown LLM provider: {name}") from exc

    def list_names(self) -> list[str]:
        """
        Return names for provider-independent language-model invocation.

        The class owns any required collaborators or state; arguments supply only the
        data needed for this call.
        """
        return sorted(self._providers)
