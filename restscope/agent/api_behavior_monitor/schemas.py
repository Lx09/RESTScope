"""Public result contracts for API Behavior Monitor orchestration."""

from __future__ import annotations

from dataclasses import dataclass

from .contract_tracker import ContractCheckResult
from .resource_schemas import ResourceMonitorResult
from .response_value import ResponseValueObservationResult


@dataclass(frozen=True, slots=True)
class APIBehaviorWarning:
    code: str
    message: str
    issues: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class APIBehaviorMonitorResult:
    contract: ContractCheckResult
    resource_identifier: ResourceMonitorResult | None = None
    response_values: ResponseValueObservationResult | None = None
    warnings: tuple[APIBehaviorWarning, ...] = ()
