"""Exceptions raised by the provider-neutral LLM layer."""

from __future__ import annotations


class LLMError(Exception):
    """Base class for LLM module failures."""


class UnknownProviderError(LLMError):
    """Raised when a request references an unregistered provider."""


class ProviderInvokeError(LLMError):
    """Raised when a provider call fails."""


class StrictToolUnavailableError(ProviderInvokeError):
    """A provider cannot serve a requested strict function call.

    Agents may catch this narrow error when they own an explicitly bounded
    fallback representation. Authentication, permission, and rate-limit
    failures deliberately remain ordinary :class:`ProviderInvokeError`
    instances so a fallback cannot hide account or capacity problems.

    Args:
        code: Stable, provider-safe reason suitable for traces and tests.
        message: Human-readable detail that must not contain credentials.
    """

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


class ProviderAuthError(ProviderInvokeError):
    """Raised when provider credentials are rejected or missing."""


class InvalidProviderResponseError(LLMError):
    """Raised when a provider response cannot be normalized."""
