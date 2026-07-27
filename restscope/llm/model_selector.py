"""Role-to-model selection for the dual-model configuration."""

from __future__ import annotations

from restscope.llm.schemas import LLMModelConfig, LLMReasoningConfig


class ModelSelector:
    """Map semantic Agent roles to one of the two configured model profiles.

    Callers request a role rather than a provider/model name. This keeps model
    choice centralized and applies deterministic temperature zero to structured
    diagnosis, patch, and effect-validation protocols.
    """

    THINKING_ROLES = {
        "planner",
        "result_analyst",
        "check_designer",
        "intelligence_updater",
        "operation_smoke_root_cause_diagnosis",
        "operation_smoke_effect_validation",
    }
    FAST_ROLES = {
        "api_behavior_monitor",
        "decision_maker",
        "parameter_patch_agent",
    }
    ZERO_TEMPERATURE_ROLES = {
        "operation_smoke_root_cause_diagnosis",
        "operation_smoke_effect_validation",
        "parameter_patch_agent",
    }

    def __init__(self, *, thinking: LLMModelConfig, fast: LLMModelConfig) -> None:
        self.thinking = thinking
        self.fast = fast

    @classmethod
    def from_config(cls, llm_config) -> "ModelSelector":
        """Translate application settings into thinking and fast runtime profiles."""
        thinking = cls._from_model_config("thinking", llm_config.thinking)
        fast = cls._from_model_config("fast", llm_config.fast)
        return cls(thinking=thinking, fast=fast)

    def select(self, role: str) -> LLMModelConfig:
        """Return a copied profile labeled and adjusted for the requested role."""
        if role in self.FAST_ROLES:
            selected = self.fast
        elif role in self.THINKING_ROLES:
            selected = self.thinking
        else:
            raise ValueError(f"Unsupported LLM role: {role}")
        update = {"role": role}
        if role in self.ZERO_TEMPERATURE_ROLES:
            update["temperature"] = 0
        return selected.model_copy(update=update)

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
