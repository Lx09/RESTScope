"""RESTScope LLM module."""

from .client import LLMClient
from .config import build_llm_client, build_llm_registry
from .exceptions import (
    InvalidProviderResponseError,
    LLMError,
    OutputValidationError,
    ProviderAuthError,
    ProviderInvokeError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    UnknownProviderError,
)
from .model_selector import ModelSelector
from .output_validator import OutputValidator
from .redactor import Redactor
from .registry import LLMProviderRegistry
from .request_factory import LLMRequestFactory
from .schemas import (
    LLMMessage,
    LLMModelConfig,
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
    "OutputValidationError",
    "ProviderAuthError",
    "ProviderInvokeError",
    "ProviderRateLimitError",
    "ProviderTimeoutError",
    "UnknownProviderError",
    "ModelSelector",
    "OutputValidator",
    "Redactor",
    "LLMProviderRegistry",
    "LLMRequestFactory",
    "LLMMessage",
    "LLMModelConfig",
    "LLMRequest",
    "LLMResponse",
    "ToolCall",
    "ToolResult",
    "ToolSpec",
    "ValidationIssue",
    "ValidationResult",
]
