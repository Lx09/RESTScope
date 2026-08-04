"""Expose RESTScope's provider-neutral language-model Interface.

Workflow Agents import request/response schemas, the synchronous client,
configuration builders, and stable errors from here. Concrete SDK translation
remains inside ``restscope.llm.providers``.
"""

from .client import LLMClient
from .config import build_llm_client, build_llm_registry
from .exceptions import (
    InvalidProviderResponseError,
    LLMError,
    ProviderAuthError,
    ProviderInvokeError,
    ProviderUnavailableError,
    StrictToolUnavailableError,
    UnknownProviderError,
)
from .model_selector import ModelSelector
from .output_validator import OutputValidator
from .registry import LLMProviderRegistry
from .schemas import (
    LLMMessage,
    LLMModelConfig,
    LLMReasoningConfig,
    LLMRequest,
    LLMResponse,
    ToolCall,
    ToolResult,
    ToolSpec,
    ValidationIssue,
    ValidationResult,
)

__all__ = [
    "LLMClient",
    "build_llm_client",
    "build_llm_registry",
    "InvalidProviderResponseError",
    "LLMError",
    "ProviderAuthError",
    "ProviderInvokeError",
    "ProviderUnavailableError",
    "StrictToolUnavailableError",
    "UnknownProviderError",
    "ModelSelector",
    "OutputValidator",
    "LLMProviderRegistry",
    "LLMMessage",
    "LLMModelConfig",
    "LLMReasoningConfig",
    "LLMRequest",
    "LLMResponse",
    "ToolCall",
    "ToolResult",
    "ToolSpec",
    "ValidationIssue",
    "ValidationResult",
]
