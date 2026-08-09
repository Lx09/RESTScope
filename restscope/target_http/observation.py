"""Describe bounded target responses and their optional behavior observation.

Callers attach an OpenAPI operation context to a request. The transport turns
the bounded response into :class:`TargetResponseObservation` and offers it to
one processor, typically the API Behavior Monitor. The processor is advisory:
its warnings accompany the real HTTP result and never replace it.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Literal, Protocol


@dataclass(slots=True, frozen=True)
class TargetResponseOperationContext:
    """Attach OpenAPI identity needed to interpret one target response."""

    ir: object
    operation_key: str | None = None
    operation_method: str | None = None
    operation_path: str | None = None


@dataclass(slots=True, frozen=True)
class TargetOperationIdentity:
    """Identify the exact operation around one internal HTTP invocation."""

    operation_key: str
    method: str
    path: str


_TARGET_OPERATION_IDENTITY: ContextVar[TargetOperationIdentity | None] = ContextVar(
    "restscope_target_operation_identity",
    default=None,
)


@contextmanager
def target_operation_scope(identity: TargetOperationIdentity) -> Iterator[None]:
    """Bind an operation identity without exposing it as a model argument.

    The ``finally`` reset prevents an internal Probe identity from leaking into
    a later ordinary HTTP Tool call, including when the request raises.
    """

    token = _TARGET_OPERATION_IDENTITY.set(identity)
    try:
        yield
    finally:
        _TARGET_OPERATION_IDENTITY.reset(token)


def current_target_operation_identity() -> TargetOperationIdentity | None:
    """Return the internal operation identity bound to this execution context."""

    return _TARGET_OPERATION_IDENTITY.get()


@dataclass(slots=True, frozen=True)
class TargetResponseObservation:
    """Carry bounded response evidence to one synchronous processor."""

    method: str
    path: str
    url: str
    status_code: int
    reason_phrase: str
    headers: Mapping[str, str]
    body: bytes
    body_truncated: bool


@dataclass(slots=True, frozen=True)
class TargetResponseProcessorWarning:
    """Describe a processor failure without replacing the HTTP result."""

    code: str
    message: str
    issues: tuple[str, ...] = ()


@dataclass(slots=True, frozen=True)
class TargetResponseProcessorResult:
    """Carry the bounded outcome from a synchronous response processor."""

    response_validation: Literal["evaluated", "partial", "not_evaluated"]
    warnings: tuple[TargetResponseProcessorWarning, ...] = ()
    details: Mapping[str, Any] | None = None


class TargetResponseProcessor(Protocol):
    """Allow transport to notify a monitor without owning that monitor."""

    def process(
        self,
        observation: TargetResponseObservation,
        context: TargetResponseOperationContext,
    ) -> TargetResponseProcessorResult | TargetResponseProcessorWarning | None:
        """Inspect one bounded response and return advisory structured output."""


@dataclass(slots=True, frozen=True)
class BufferedTargetResponse:
    """Return response metadata and an optional body after the stream closes."""

    status_code: int
    reason_phrase: str
    url: str
    headers: Mapping[str, str]
    encoding: str | None
    body: bytes | None
    body_truncated: bool
    processor_result: TargetResponseProcessorResult | None
