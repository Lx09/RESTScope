"""Role-to-model selection for the dual-model configuration."""

from __future__ import annotations

from typing import Literal

from restscope.llm.schemas import LLMModelConfig


LLMRoleName = Literal["planner", "result_analyst", "decision_maker", "check_designer", "intelligence_updater"]


class ModelSelector:
    """Map context roles to thinking or fast model settings."""

    THINKING_ROLES = {"planner", "result_analyst", "check_designer", "intelligence_updater"}
    FAST_ROLES = {"decision_maker"}

    def __init__(self, *, thinking: LLMModelConfig, fast: LLMModelConfig) -> None:
        self.thinking = thinking
        self.fast = fast

    @classmethod
    def from_config(cls, llm_config) -> "ModelSelector":
        thinking = cls._from_model_config("thinking", llm_config.thinking)
        fast = cls._from_model_config("fast", llm_config.fast)
        return cls(thinking=thinking, fast=fast)

    def select(self, role: str) -> LLMModelConfig:
        if role in self.FAST_ROLES:
            return self.fast.model_copy(update={"role": role})
        if role in self.THINKING_ROLES:
            return self.thinking.model_copy(update={"role": role})
        raise ValueError(f"Unsupported LLM role: {role}")

    @staticmethod
    def _from_model_config(role: str, raw_config) -> LLMModelConfig:
        return LLMModelConfig(
            role=role,
            provider=getattr(raw_config, "provider", "openai_compatible"),
            model=raw_config.model,
            temperature=raw_config.temperature,
            max_tokens=raw_config.max_tokens,
            timeout_seconds=raw_config.timeout,
            response_format="json_schema",
            tool_choice="none",
            enabled=bool(raw_config.model),
        )
