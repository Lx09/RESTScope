"""HTTP transport adapter for the API Behavior Monitor Coordinator."""

from __future__ import annotations

from typing import Literal

from restscope.target_api import (
    TargetResponseObservation,
    TargetResponseOperationContext,
    TargetResponseProcessorResult,
    TargetResponseProcessorWarning,
    TargetTransportObservation,
)
from restscope.target_api.observation import TargetReplayDirective

from .coordinator import APIBehaviorMonitorCoordinator, APIBehaviorMonitorError


class APIBehaviorResponseProcessor:
    """Adapt target results to the ordered API Behavior Monitor pipeline."""

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
        except Exception as exc:  # noqa: BLE001
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
        replay_directive = (
            TargetReplayDirective(
                primary_observation_id=result.oracle_primary.primary_observation_id,
                state=result.oracle_primary,
            )
            if result.oracle_primary is not None
            and result.oracle_primary.replay_required
            else None
        )
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
            replay_directive=replay_directive,
        )

    def process_transport(
        self,
        observation: TargetTransportObservation,
        context: TargetResponseOperationContext,
    ) -> TargetResponseProcessorResult:
        """Persist a transport failure without replacing its original exception."""

        try:
            result = self.coordinator.observe_transport(observation, context)
        except APIBehaviorMonitorError as exc:
            return TargetResponseProcessorResult(
                response_validation="partial",
                warnings=(
                    TargetResponseProcessorWarning(code=exc.code, message=str(exc)),
                ),
            )
        except Exception as exc:  # noqa: BLE001
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
        return TargetResponseProcessorResult(
            response_validation="evaluated",
            details=_result_details(result),
        )


def _result_details(result) -> dict[str, object]:
    """Project stage outcomes without exposing persisted request or response JSON."""
    resource = result.resources
    assessment = result.oracle_assessment
    return {
        "operation_key": result.operation_id,
        "status_code": (
            result.contract.key.status_code if result.contract is not None else None
        ),
        "media_type": (
            result.contract.key.media_type if result.contract is not None else None
        ),
        "contract_status": (
            result.contract.status if result.contract is not None else "check_error"
        ),
        "contract_changes": (
            list(result.contract.changes) if result.contract is not None else []
        ),
        "observation_id": result.observation_id,
        "bug_found": assessment.is_bug if assessment is not None else None,
        "bug_categories": (
            [
                check.name
                for check in assessment.checks
                if check.status == "reproduced"
            ]
            if assessment is not None
            else []
        ),
        "resources": (
            {
                "resources_updated": len(resource.resources),
                "instances_updated": len(resource.instances),
                "conflicts": list(resource.conflicts),
            }
            if resource is not None
            else None
        ),
        "warning_codes": [warning.code for warning in result.warnings],
    }
