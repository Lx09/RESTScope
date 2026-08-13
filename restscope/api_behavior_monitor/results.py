"""Public result contracts for one API response monitoring pass.

The Coordinator reports the Contract Monitor outcome, the durable observation
identity, optional resource updates, and bounded warnings.  It never returns a
raw response body or resource state to Agent-facing callers.
"""

from __future__ import annotations

from dataclasses import dataclass

from .catalog import OracleAssessment, ResourceDerivationResult
from .contract_monitor import ContractCheckResult
from .oracle import OraclePrimaryDecision


@dataclass(frozen=True, slots=True)
class APIBehaviorWarning:
    """Expose one bounded monitor warning with a stable code and safe issues."""

    code: str
    message: str
    issues: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class APIBehaviorMonitorResult:
    """Summarize independent Contract, observation, and resource outcomes."""

    operation_id: str
    contract: ContractCheckResult | None
    observation_id: str | None = None
    resources: ResourceDerivationResult | None = None
    warnings: tuple[APIBehaviorWarning, ...] = ()
    oracle_primary: OraclePrimaryDecision | None = None
    oracle_assessment: OracleAssessment | None = None
