"""Factories for configured LLM services."""

from __future__ import annotations

from restscope.llm.client import LLMClient
from restscope.llm.providers.fake import FakeProvider
from restscope.llm.providers.openai_compatible import OpenAICompatibleProvider
from restscope.llm.registry import LLMProviderRegistry


def build_llm_registry(config) -> LLMProviderRegistry:
    """Build a registry from the short dual-model RESTScope config."""

    registry = LLMProviderRegistry()
    registry.register(FakeProvider())

    configs = [config.thinking, config.fast]
    openai_configs = [item for item in configs if getattr(item, "provider", "") == "openai_compatible"]
    selected = next((item for item in openai_configs if item.api_key), None)
    if selected is not None:
        registry.register(
            OpenAICompatibleProvider(
                api_key=selected.api_key,
                base_url=selected.base_url or None,
            )
        )

    return registry


def build_llm_client(config) -> LLMClient:
    """Build an LLMClient from the short dual-model RESTScope config."""

    return LLMClient(build_llm_registry(config))
