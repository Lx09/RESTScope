"""Factories for configured LLM services."""

from __future__ import annotations

from restscope.llm.client import LLMClient
from restscope.llm.providers.deepseek import DeepSeekProvider
from restscope.llm.providers.fake import FakeProvider
from restscope.llm.providers.openai_compatible import OpenAICompatibleProvider
from restscope.llm.registry import LLMProviderRegistry
from restscope.llm.schemas import LLMReasoningConfig


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

    deepseek_configs = [item for item in configs if getattr(item, "provider", "") == "deepseek"]
    selected_deepseek = next((item for item in deepseek_configs if item.api_key), None)
    if selected_deepseek is not None:
        registry.register(
            DeepSeekProvider(
                api_key=selected_deepseek.api_key,
                base_url=selected_deepseek.base_url or None,
                default_reasoning=LLMReasoningConfig(
                    mode=getattr(selected_deepseek, "reasoning_mode", "enabled"),
                    effort=getattr(selected_deepseek, "reasoning_effort", None),
                ),
            )
        )

    return registry


def build_llm_client(config) -> LLMClient:
    """Build an LLMClient from the short dual-model RESTScope config."""

    return LLMClient(build_llm_registry(config))
