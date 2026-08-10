"""Factories for configured LLM services."""

from __future__ import annotations

from restscope.llm.client import LLMClient
from restscope.llm.providers.deepseek import DeepSeekProvider
from restscope.llm.providers.openai_compatible import OpenAICompatibleProvider
from restscope.llm.registry import LLMProviderRegistry
from restscope.llm.schemas import LLMModelConfig, LLMReasoningConfig
from restscope.observability import TracingRuntime


def build_llm_registry(config) -> LLMProviderRegistry:
    """Build a registry from the short dual-model RESTScope config."""

    registry = LLMProviderRegistry()

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


def build_llm_client(
    config,
    *,
    tracing_runtime: TracingRuntime | None = None,
) -> LLMClient:
    """Build an LLMClient from the short dual-model RESTScope config."""

    runtime = tracing_runtime or TracingRuntime.disabled()
    runtime.redactor.register_secrets(
        (config.thinking.api_key, config.fast.api_key)
    )
    return LLMClient(
        build_llm_registry(config),
        tracing_runtime=runtime,
    )


def build_llm_model_config(name: str, raw_config) -> LLMModelConfig:
    """Translate one App model setting into a named Agent model configuration.

    The function performs no semantic role selection. An Agent Profile chooses
    this exact name, leaving provider and capacity settings reusable without a
    second policy layer.
    """
    return LLMModelConfig(
        name=name,
        provider=getattr(raw_config, "provider", "openai_compatible"),
        model=raw_config.model,
        temperature=raw_config.temperature,
        max_tokens=raw_config.max_tokens,
        context_window_tokens=raw_config.context_window_tokens,
        timeout_seconds=raw_config.timeout,
        response_format="json_schema",
        tool_choice="none",
        reasoning=LLMReasoningConfig(
            mode=getattr(raw_config, "reasoning_mode", "default"),
            effort=getattr(raw_config, "reasoning_effort", None),
        ),
        enabled=bool(raw_config.model),
    )
