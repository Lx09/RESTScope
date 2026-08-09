"""HTTP transport adapter for the API Behavior Monitor Coordinator."""

from __future__ import annotations

from typing import Literal

from restscope.target_http import (
    TargetResponseObservation,
    TargetResponseOperationContext,
    TargetResponseProcessorResult,
    TargetResponseProcessorWarning,
)

from .coordinator import APIBehaviorMonitorCoordinator, APIBehaviorMonitorError


class APIBehaviorResponseProcessor:
    """Adapt completed target responses to the three API Behavior Monitor subjects without owning their state."""
    def __init__(self, coordinator: APIBehaviorMonitorCoordinator) -> None:
        """Store the workflow coordinator that receives each target response."""
        self.coordinator = coordinator

    def process(
        self,
        observation: TargetResponseObservation,
        context: TargetResponseOperationContext,
    ) -> TargetResponseProcessorResult:
        """Pass one response observation to the Coordinator and return bounded validation status and warnings to target HTTP."""
        try:
            result = self.coordinator.observe_response(observation, context)
        except APIBehaviorMonitorError as exc:
            return TargetResponseProcessorResult(
                response_validation="partial",
                warnings=(
                    TargetResponseProcessorWarning(
                        code=exc.code,
                        message=str(exc),
                    ),
                ),
            )
        except Exception as exc:
            return TargetResponseProcessorResult(
                response_validation="partial",
                warnings=(
                    TargetResponseProcessorWarning(
                        code="api_behavior_monitor_failed",
                        message="API behavior monitoring failed",
                        issues=(type(exc).__name__,),
                    ),
                ),
            )
        response_validation: Literal[
            "evaluated",
            "partial",
            "not_evaluated",
        ] = "partial" if result.warnings else "evaluated"
        return TargetResponseProcessorResult(
            response_validation=response_validation,
            warnings=tuple(
                TargetResponseProcessorWarning(
                    code=warning.code,
                    message=warning.message,
                    issues=warning.issues,
                )
                for warning in result.warnings
            ),
            details=_result_details(result),
        )


def _result_details(result) -> dict:
    """Convert monitor warnings and errors into bounded response-observation details."""
    resource = result.resource_identifier
    response_values = result.response_values
    return {
        "operation_key": result.contract.key.operation_key,
        "status_code": result.contract.key.status_code,
        "media_type": result.contract.key.media_type,
        "contract_status": result.contract.status,
        "contract_changes": list(result.contract.changes),
        "resource_identifier": (
            {
                "status": resource.status,
                "groups_processed": resource.groups_processed,
                "identifiers_recorded": resource.identifiers_recorded,
                "warning_code": (
                    resource.warning.code
                    if resource.warning is not None
                    else None
                ),
            }
            if resource is not None
            else None
        ),
        "response_values": (
            {
                "sources_processed": response_values.sources_processed,
                "values_recorded": response_values.values_recorded,
            }
            if response_values is not None
            else None
        ),
        "warning_codes": [warning.code for warning in result.warnings],
    }
