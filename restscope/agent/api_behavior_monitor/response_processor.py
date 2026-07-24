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
        )
