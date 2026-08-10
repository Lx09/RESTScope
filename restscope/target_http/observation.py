"""Describe bounded target responses and their optional behavior observation.

Callers attach an OpenAPI operation context to a request. The transport turns
the bounded response into :class:`TargetResponseObservation` and offers it to
one processor, typically the API Behavior Monitor. The processor is advisory:
its warnings accompany the real HTTP result and never replace it.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, Protocol


@dataclass(slots=True, frozen=True)
class TargetResponseOperationContext:
    """Attach OpenAPI identity needed to interpret one target response."""

    ir: object
    operation_key: str | None = None
    operation_method: str | None = None
    operation_path: str | None = None


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
    details: Mapping[str, object] | None = None


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
