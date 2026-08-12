"""Coordinate Contract checks, factual observations, and resource derivation.

Every matched target response first reaches the Contract Monitor.  A complete
valid 2xx JSON response is then stored as an observation in its own transaction.
Only after that fact commits may an optional Resource Monitor derive resources
and current instances.  Each boundary fails independently so an advisory
monitor cannot replace the target's real HTTP result.
"""

from __future__ import annotations

import json
from typing import Protocol

from restscope.data_types import JSONValue
from restscope.target_http.request import is_json_media_type, normalize_media_type
from restscope.openapi_parser import (
    OpenAPIOperationMatchError,
    OpenAPISpecIR,
    match_operation,
)
from restscope.openapi_parser.ir import OperationIR
from restscope.observability import TracingRuntime
from restscope.target_http import (
    TargetResponseObservation,
    TargetResponseOperationContext,
)

from .catalog import (
    ObservationWrite,
    OperationDefinition,
    ResourceDerivationResult,
    APIBehaviorCatalog,
)
from .contract_monitor import (
    ContractCheckResult,
    ResponseContractTracker,
)
from .results import APIBehaviorMonitorResult, APIBehaviorWarning


class ResourceResponseTracker(Protocol):
    """Derive resources from one already-persisted successful JSON response."""

    def observe(
        self,
        *,
        operation: OperationDefinition,
        body: JSONValue,
    ) -> ResourceDerivationResult: ...


class APIBehaviorMonitorError(RuntimeError):
    """Report a response that cannot enter the OpenAPI-owned monitor flow."""

    def __init__(self, code: str, message: str) -> None:
        """Retain a stable processor warning code beside its readable message."""

        super().__init__(message)
        self.code = code


class APIBehaviorMonitorCoordinator:
    """Run the three response stages without sharing their transactions."""

    def __init__(
        self,
        *,
        contract_tracker: ResponseContractTracker,
        catalog: APIBehaviorCatalog,
        resource_tracker: ResourceResponseTracker | None = None,
        tracing_runtime: TracingRuntime | None = None,
    ) -> None:
        """Retain App-owned collaborators without opening persistent state."""

        self.contract_tracker = contract_tracker
        self.catalog = catalog
        self.resource_tracker = resource_tracker
        self.tracing_runtime = tracing_runtime or TracingRuntime.disabled()

    def observe_response(
        self,
        observation: TargetResponseObservation,
        context: TargetResponseOperationContext,
    ) -> APIBehaviorMonitorResult:
        """Process one response and return a bounded stage-by-stage summary.

        Raises:
            APIBehaviorMonitorError: The supplied context is not an initialized
                OpenAPI IR or the request cannot identify one current operation.
                In either case no durable Monitor row is written.
        """

        if not isinstance(context.ir, OpenAPISpecIR):
            raise APIBehaviorMonitorError(
                "api_behavior_context_invalid",
                "API behavior monitoring requires an initialized OpenAPI IR",
            )
        try:
            operation_ir = _resolve_operation(
                observation,
                context=context,
                ir=context.ir,
            )
        except OpenAPIOperationMatchError as exc:
            raise APIBehaviorMonitorError(exc.code, str(exc)) from exc

        operation = OperationDefinition(
            operation_id=operation_ir.operation_key,
            method=operation_ir.method,
            path=operation_ir.path,
            description=operation_ir.description,
        )
        media_type = normalize_media_type(observation.headers.get("content-type"))
        warnings: list[APIBehaviorWarning] = []

        with self.tracing_runtime.span(
            "APIBehaviorMonitorCoordinator.observe_response",
            kind="CHAIN",
            input_value={
                "operation_id": operation.operation_id,
                "status_code": observation.status_code,
                "media_type": media_type,
            },
        ) as span:
            try:
                # The operation must exist before either observations or
                # OpenAPI change events can reference it.
                self.catalog.ensure_operation(operation)
            except Exception as exc:
                raise APIBehaviorMonitorError(
                    "operation_persistence_failed",
                    "Failed to persist matched operation metadata",
                ) from exc

            contract = self._check_contract(
                observation=observation,
                operation=operation,
                media_type=media_type,
                ir=context.ir,
                warnings=warnings,
            )
            observation_id, parsed_body = self._record_observation(
                observation=observation,
                context=context,
                operation=operation,
                media_type=media_type,
                warnings=warnings,
            )

            resources: ResourceDerivationResult | None = None
            if (
                observation_id is not None
                and parsed_body is not None
                and isinstance(parsed_body, (dict, list))
                and self.resource_tracker is not None
            ):
                try:
                    resources = self.resource_tracker.observe(
                        operation=operation,
                        body=parsed_body,
                    )
                except Exception as exc:
                    warnings.append(
                        APIBehaviorWarning(
                            code="resource_derivation_failed",
                            message="Resource derivation failed",
                            issues=(type(exc).__name__,),
                        )
                    )

            result = APIBehaviorMonitorResult(
                operation_id=operation.operation_id,
                contract=contract,
                observation_id=observation_id,
                resources=resources,
                warnings=tuple(warnings),
            )
            span.set_output(
                {
                    "operation_id": result.operation_id,
                    "observation_recorded": result.observation_id is not None,
                    "warning_count": len(result.warnings),
                }
            )
            return result

    def _check_contract(
        self,
        *,
        observation: TargetResponseObservation,
        operation: OperationDefinition,
        media_type: str | None,
        ir: OpenAPISpecIR,
        warnings: list[APIBehaviorWarning],
    ) -> ContractCheckResult | None:
        """Check every response while isolating Contract Monitor failures."""

        try:
            result = self.contract_tracker.observe(
                ir=ir,
                operation_key=operation.operation_id,
                status_code=observation.status_code,
                media_type=media_type,
                body=observation.body,
                body_truncated=observation.body_truncated,
            )
        except Exception as exc:
            warnings.append(
                APIBehaviorWarning(
                    code="response_contract_check_failed",
                    message="Response contract checking failed",
                    issues=(type(exc).__name__,),
                )
            )
            return None
        if result.status == "pending_retry":
            warnings.append(
                APIBehaviorWarning(
                    code="response_contract_pending_retry",
                    message=(
                        "Response contract could not be checked because its "
                        "declared JSON body was invalid or incomplete"
                    ),
                )
            )
        return result

    def _record_observation(
        self,
        *,
        observation: TargetResponseObservation,
        context: TargetResponseOperationContext,
        operation: OperationDefinition,
        media_type: str | None,
        warnings: list[APIBehaviorWarning],
    ) -> tuple[str | None, JSONValue | None]:
        """Store one eligible response exactly, returning its parsed JSON value."""

        if (
            not 200 <= observation.status_code <= 299
            or observation.body_truncated
            or not is_json_media_type(media_type)
        ):
            return None, None
        try:
            response_json = observation.body.decode("utf-8")
            parsed: JSONValue = json.loads(response_json)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None, None
        try:
            record = self.catalog.record_observation(
                ObservationWrite(
                    operation_id=operation.operation_id,
                    timestamp=observation.received_at,
                    status_code=observation.status_code,
                    media_type=media_type or "application/json",
                    request_json=observation.request_json,
                    response_json=response_json,
                    abstract_test_case_id=context.abstract_test_case_id,
                )
            )
        except Exception as exc:
            warnings.append(
                APIBehaviorWarning(
                    code="response_observation_persistence_failed",
                    message="Successful JSON observation could not be persisted",
                    issues=(type(exc).__name__,),
                )
            )
            return None, None
        return record.observation_id, parsed


def _resolve_operation(
    observation: TargetResponseObservation,
    *,
    context: TargetResponseOperationContext,
    ir: OpenAPISpecIR,
) -> OperationIR:
    """Resolve a generated operation key or match an ordinary concrete request."""

    if context.operation_key is None:
        return match_operation(ir, method=observation.method, path=observation.path)
    operation = ir.operations.get(context.operation_key)
    if operation is None:
        raise OpenAPIOperationMatchError(
            "operation_not_found",
            f"Operation {context.operation_key!r} is not present in the current IR",
        )
    return operation
