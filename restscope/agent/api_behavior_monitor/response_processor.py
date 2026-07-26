"""HTTP transport adapter for the API Behavior Monitor Agent."""

from __future__ import annotations

from restscope.http_transport import (
    TargetResponseObservation,
    TargetResponseOperationContext,
    TargetResponseProcessorResult,
    TargetResponseProcessorWarning,
)

from .agent import APIBehaviorMonitorAgent, APIBehaviorMonitorError


class APIBehaviorResponseProcessor:
    def __init__(self, agent: APIBehaviorMonitorAgent) -> None:
        self.agent = agent

    def process(
        self,
        observation: TargetResponseObservation,
        context: TargetResponseOperationContext,
    ) -> TargetResponseProcessorResult:
        try:
            result = self.agent.observe_response(observation, context)
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
        return TargetResponseProcessorResult(
            response_validation=(
                "partial" if result.warnings else "evaluated"
            ),
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
                "groups_processed": getattr(
                    resource,
                    "groups_processed",
                    0,
                ),
                "identifiers_recorded": getattr(
                    resource,
                    "identifiers_recorded",
                    0,
                ),
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
