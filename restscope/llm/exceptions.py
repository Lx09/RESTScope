"""Exceptions raised by the provider-neutral LLM layer."""

from __future__ import annotations


class LLMError(Exception):
    """Base class for LLM module failures."""


class UnknownProviderError(LLMError):
    """Raised when a request references an unregistered provider."""


class ProviderInvokeError(LLMError):
    """Raised when a provider call fails."""


class ProviderTimeoutError(ProviderInvokeError):
    """Raised when a provider call times out."""


class ProviderRateLimitError(ProviderInvokeError):
    """Raised when a provider reports rate limiting."""


class ProviderAuthError(ProviderInvokeError):
    """Raised when provider credentials are rejected or missing."""


class InvalidProviderResponseError(LLMError):
    """Raised when a provider response cannot be normalized."""


class OutputValidationError(LLMError):
    """Raised when structured output cannot be validated."""
