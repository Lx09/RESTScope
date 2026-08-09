"""Public result contracts for API Behavior Monitor orchestration."""

from __future__ import annotations

from dataclasses import dataclass

from .response_contracts import ContractCheckResult
from .resource_identifiers.schemas import ResourceMonitorResult
from .response_values.tracker import ResponseValueObservationResult


@dataclass(frozen=True, slots=True)
class APIBehaviorWarning:
    """Expose one bounded monitor warning with a stable code, message, and optional issue list."""
    code: str
    message: str
    issues: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class APIBehaviorMonitorResult:
    """Summarize contract, resource-identifier, and response-value observations for one target response."""
    contract: ContractCheckResult
    resource_identifier: ResourceMonitorResult | None = None
    response_values: ResponseValueObservationResult | None = None
    warnings: tuple[APIBehaviorWarning, ...] = ()
