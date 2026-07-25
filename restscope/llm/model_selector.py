"""Role-to-model selection for the dual-model configuration."""

from __future__ import annotations

from restscope.llm.schemas import LLMModelConfig, LLMReasoningConfig


class ModelSelector:
    """Map context roles to thinking or fast model settings."""

    THINKING_ROLES = {
        "planner",
        "result_analyst",
        "check_designer",
        "intelligence_updater",
    }
    FAST_ROLES = {
        "api_behavior_monitor",
        "decision_maker",
        "operation_smoke_generator_patch",
        "operation_smoke_parameter_diagnosis",
    }

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
            reasoning=LLMReasoningConfig(
                mode=getattr(raw_config, "reasoning_mode", "default"),
                effort=getattr(raw_config, "reasoning_effort", None),
            ),
            enabled=bool(raw_config.model),
        )
