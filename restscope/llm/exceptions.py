"""Define failures that cross the provider-neutral LLM Interface.

Provider Adapters convert SDK exceptions into these types before workflow code
sees them. Callers use the stable class and fields to choose a stop path; raw
provider response data remains inside the Adapter exception chain.
"""

from __future__ import annotations


class LLMError(Exception):
    """Base class for LLM module failures."""


class UnknownProviderError(LLMError):
    """Raised when a request references an unregistered provider."""


class ProviderInvokeError(LLMError):
    """Raised when a provider call fails."""


class ProviderUnavailableError(ProviderInvokeError):
    """Report a transient provider failure after bounded SDK retries.

    This error is safe to expose in workflow results and traces. It keeps only
    a stable classification, an optional HTTP status, and the configured SDK
    retry limit; the provider response stays on the internal exception cause.

    Args:
        status_code: HTTP status returned by the provider, when one exists.
        retry_limit: Number of retries configured for the single model request.
    """

    code = "provider_unavailable"

    def __init__(self, *, status_code: int | None, retry_limit: int) -> None:
        self.status_code = status_code
        self.retry_limit = retry_limit
        status_detail = f" (HTTP {status_code})" if status_code is not None else ""
        super().__init__(
            f"{self.code}: Model provider remained unavailable after "
            f"{retry_limit} SDK retries{status_detail}."
        )


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
