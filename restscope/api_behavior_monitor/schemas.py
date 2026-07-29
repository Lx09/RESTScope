"""Public result contracts for API Behavior Monitor orchestration."""

from __future__ import annotations

from dataclasses import dataclass

from .contract_tracker import ContractCheckResult
from .resource_schemas import ResourceMonitorResult
from .response_value import ResponseValueObservationResult


@dataclass(frozen=True, slots=True)
class APIBehaviorWarning:
    """
    Coordinate apibehavior warning behavior for API response monitoring and its narrowly
    approved evidence catalog.

    Read the public methods as the supported lifecycle and treat underscore-prefixed
    helpers as internal implementation details.
    """
    code: str
    message: str
    issues: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class APIBehaviorMonitorResult:
    """
    Carry validated apibehavior monitor result data across API response monitoring and
    its narrowly approved evidence catalog.

    The annotated fields form the contract; validation rejects missing, extra, or
    incorrectly typed values at the boundary.
    """
    contract: ContractCheckResult
    resource_identifier: ResourceMonitorResult | None = None
    response_values: ResponseValueObservationResult | None = None
    warnings: tuple[APIBehaviorWarning, ...] = ()
