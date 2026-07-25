"""Synchronous coordinator for observed API response behavior."""

from __future__ import annotations

import json
from typing import Any

from restscope.http_transport import (
    TargetResponseObservation,
    TargetResponseOperationContext,
)
from restscope.openapi_parser import (
    OpenAPIOperationMatchError,
    OpenAPISpecIR,
    match_operation,
)
from restscope.openapi_parser.ir import OperationIR, SchemaIR
from restscope.observability import TracingRuntime

from .contract_tracker import (
    ContractCheckResult,
    ResponseContractError,
    ResponseContractTracker,
    normalize_media_type,
)
from .resource_identifier import (
    ResourceIdentifierOutputError,
    ResourceIdentifierTracker,
)
from .resource_schemas import (
    MonitoredOperation,
    ResourceLookupRequest,
    ResourceLookupResult,
    ResourceMonitorResult,
    ResourceObservation,
)
from .response_value import (
    ResponseValueObservationResult,
    ResponseValuePreview,
    ResponseValueRegistrationResult,
    ResponseValueSourceOption,
    ResponseValueTracker,
)
from .response_value_catalog import ResponseValueSource
from .schemas import APIBehaviorMonitorResult, APIBehaviorWarning


class APIBehaviorMonitorError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class APIBehaviorMonitorAgent:
    """Update response IR first, then collect successful reusable evidence."""

    def __init__(
        self,
        *,
        contract_tracker: ResponseContractTracker,
        resource_identifier_tracker: ResourceIdentifierTracker,
        response_value_tracker: ResponseValueTracker,
        tracing_runtime: TracingRuntime | None = None,
    ) -> None:
        self.contract_tracker = contract_tracker
        self.resource_identifier_tracker = resource_identifier_tracker
        self.response_value_tracker = response_value_tracker
        self._tracing_runtime = (
            tracing_runtime
            or getattr(resource_identifier_tracker, "tracing_runtime", None)
            or TracingRuntime.disabled()
        )

    @property
    def catalog(self):
        return self.resource_identifier_tracker.catalog

    @property
    def client(self):
        return self.resource_identifier_tracker.client

    @property
    def tracing_runtime(self):
        return self._tracing_runtime

    @tracing_runtime.setter
    def tracing_runtime(self, value) -> None:
        self._tracing_runtime = value
        if hasattr(self.resource_identifier_tracker, "tracing_runtime"):
            self.resource_identifier_tracker.tracing_runtime = value

    def observe_response(
        self,
        observation: TargetResponseObservation,
        context: TargetResponseOperationContext,
    ) -> APIBehaviorMonitorResult:
        media_type = normalize_media_type(
            observation.headers.get("content-type")
        )
        attributes: dict[str, Any] = {
            "http.request.method": observation.method,
            "http.response.status_code": observation.status_code,
        }
        if context.operation_key is not None:
            attributes["restscope.operation.key"] = context.operation_key
        with self.tracing_runtime.span(
            "APIBehaviorMonitorAgent.observe_response",
            kind="AGENT",
            input_value={
                "operation_key": context.operation_key,
                "method": observation.method,
                "path": observation.path,
                "status_code": observation.status_code,
                "media_type": media_type,
                "body_truncated": observation.body_truncated,
            },
            attributes=attributes,
        ) as span:
            result = self._observe_response(observation, context)
            span.set_output(_monitor_trace_summary(result))
            span.set_attribute(
                "restscope.operation.key",
                result.contract.key.operation_key,
            )
            span.set_attribute(
                "restscope.response_contract.status",
                result.contract.status,
            )
            span.set_attribute(
                "restscope.response_contract.change_count",
                len(result.contract.changes),
            )
            span.set_attribute(
                "restscope.behavior_monitor.warning_count",
                len(result.warnings),
            )
            if result.resource_identifier is not None:
                span.set_attribute(
                    "restscope.resource_monitor.status",
                    result.resource_identifier.status,
                )
                span.set_attribute(
                    "restscope.resource_monitor.identifiers_recorded",
                    getattr(
                        result.resource_identifier,
                        "identifiers_recorded",
                        0,
                    ),
                )
            if result.response_values is not None:
                span.set_attribute(
                    "restscope.response_value.values_recorded",
                    result.response_values.values_recorded,
                )
            return result

    def _observe_response(
        self,
        observation: TargetResponseObservation,
        context: TargetResponseOperationContext,
    ) -> APIBehaviorMonitorResult:
        if not isinstance(context.ir, OpenAPISpecIR):
            raise APIBehaviorMonitorError(
                "api_behavior_context_invalid",
                "API behavior monitoring requires an initialized OpenAPI IR",
            )
        try:
            operation, operation_ir = _resolve_operation(
                observation,
                context=context,
                ir=context.ir,
            )
        except (OpenAPIOperationMatchError, ResponseContractError) as exc:
            raise APIBehaviorMonitorError(exc.code, str(exc)) from exc

        media_type = normalize_media_type(
            observation.headers.get("content-type")
        )
        try:
            contract = self.contract_tracker.observe(
                ir=context.ir,
                operation_key=operation.operation_key,
                status_code=observation.status_code,
                media_type=media_type,
                body=observation.body,
                body_truncated=observation.body_truncated,
            )
        except ResponseContractError as exc:
            raise APIBehaviorMonitorError(exc.code, str(exc)) from exc

        warnings: list[APIBehaviorWarning] = []
        if contract.status == "pending_retry":
            warnings.append(
                APIBehaviorWarning(
                    code="response_contract_pending_retry",
                    message=(
                        "Response contract could not be checked because its JSON "
                        "body was invalid or truncated"
                    ),
                )
            )

        if (
            not 200 <= observation.status_code < 300
            or observation.body_truncated
            or not _is_json_media_type(media_type)
        ):
            return APIBehaviorMonitorResult(
                contract=contract,
                warnings=tuple(warnings),
            )
        try:
            body = json.loads(observation.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return APIBehaviorMonitorResult(
                contract=contract,
                warnings=tuple(warnings),
            )

        resource_result: ResourceMonitorResult | None = None
        try:
            resource_result = self.resource_identifier_tracker.observe(
                ResourceObservation(
                    operation=operation,
                    status_code=observation.status_code,
                    media_type=media_type,
                    body=body,
                    response_schema_fields=_response_schema_fields(
                        operation_ir,
                        status_code=observation.status_code,
                        media_type=media_type,
                    ),
                )
            )
        except ResourceIdentifierOutputError as exc:
            warnings.append(
                APIBehaviorWarning(code=exc.code, message=str(exc))
            )
        except Exception as exc:
            warnings.append(
                APIBehaviorWarning(
                    code="resource_identifier_failed",
                    message="Resource identifier tracking failed",
                    issues=(type(exc).__name__,),
                )
            )
        else:
            if resource_result.warning is not None:
                warnings.append(
                    APIBehaviorWarning(
                        code=resource_result.warning.code,
                        message=resource_result.warning.message,
                        issues=tuple(resource_result.warning.issues),
                    )
                )

        try:
            if contract.status == "updated":
                self.response_value_tracker.refresh_sources(
                    ir=context.ir,
                    producer_operation_key=operation.operation_key,
                )
            value_result = self.response_value_tracker.observe(
                producer_operation_key=operation.operation_key,
                status_code=observation.status_code,
                media_type=media_type,
                body=body,
            )
        except Exception as exc:
            value_result = None
            warnings.append(
                APIBehaviorWarning(
                    code="response_value_tracking_failed",
                    message="Response value tracking failed",
                    issues=(type(exc).__name__,),
                )
            )
        return APIBehaviorMonitorResult(
            contract=contract,
            resource_identifier=resource_result,
            response_values=value_result,
            warnings=tuple(warnings),
        )

    def lookup(self, request: ResourceLookupRequest) -> ResourceLookupResult:
        return self.resource_identifier_tracker.lookup(request)

    def register_response_value(
        self,
        *,
        ir: OpenAPISpecIR,
        consumer_operation_key: str,
        consumer_input_node_id: str,
        parameter_name: str,
        expected_type: str | None,
    ) -> ResponseValueRegistrationResult:
        return self.response_value_tracker.register(
            ir=ir,
            consumer_operation_key=consumer_operation_key,
            consumer_input_node_id=consumer_input_node_id,
            parameter_name=parameter_name,
            expected_type=expected_type,
        )

    def response_values_for(self, value_name: str) -> list[object]:
        return self.response_value_tracker.catalog.values_for(value_name)

    def available_response_value_sources(
        self,
        *,
        ir: OpenAPISpecIR,
        consumer_operation_key: str,
        consumer_input_node_id: str,
        expected_type: str | None,
    ) -> list[ResponseValueSourceOption]:
        return self.response_value_tracker.available_source_options(
            ir=ir,
            consumer_operation_key=consumer_operation_key,
            consumer_input_node_id=consumer_input_node_id,
            expected_type=expected_type,
        )

    def register_response_value_sources(
        self,
        *,
        consumer_operation_key: str,
        consumer_input_node_id: str,
        parameter_name: str,
        expected_type: str | None,
        sources: list[ResponseValueSource],
    ) -> ResponseValueRegistrationResult:
        return self.response_value_tracker.register_selected_sources(
            consumer_operation_key=consumer_operation_key,
            consumer_input_node_id=consumer_input_node_id,
            parameter_name=parameter_name,
            expected_type=expected_type,
            sources=sources,
        )

    def preview_response_value(
        self,
        *,
        ir: OpenAPISpecIR,
        consumer_operation_key: str,
        consumer_input_node_id: str,
        parameter_name: str,
        expected_type: str | None,
    ) -> ResponseValuePreview | None:
        return self.response_value_tracker.preview(
            ir=ir,
            consumer_operation_key=consumer_operation_key,
            consumer_input_node_id=consumer_input_node_id,
            parameter_name=parameter_name,
            expected_type=expected_type,
        )


def _resolve_operation(
    observation: TargetResponseObservation,
    *,
    context: TargetResponseOperationContext,
    ir: OpenAPISpecIR,
) -> tuple[MonitoredOperation, OperationIR]:
    if context.operation_key is not None:
        operation_ir = ir.operations.get(context.operation_key)
        if operation_ir is None:
            raise ResponseContractError(
                "operation_not_found",
                f"Operation {context.operation_key!r} is not present in the current IR",
            )
    else:
        operation_ir = match_operation(
            ir,
            method=observation.method,
            path=observation.path,
        )
    return (
        MonitoredOperation(
            operation_key=operation_ir.operation_key,
            method=context.operation_method or operation_ir.method,
            path=context.operation_path or operation_ir.path,
        ),
        operation_ir,
    )


def _response_schema_fields(
    operation: OperationIR,
    *,
    status_code: int,
    media_type: str | None,
) -> list[dict[str, Any]]:
    response = _response_for_status(operation, status_code)
    if response is None:
        return []
    media = response.contents.get(media_type) if media_type is not None else None
    if media is None:
        media = next(
            (
                item
                for name, item in response.contents.items()
                if _is_json_media_type(name) and item.schema is not None
            ),
            None,
        )
    if media is None or media.schema is None:
        return []
    return _schema_fields(media.schema)


def _response_for_status(operation: OperationIR, status_code: int):
    responses = operation.responses.by_status
    exact = responses.get(str(status_code))
    if exact is not None:
        return exact
    wildcard = f"{status_code // 100}XX"
    for key, response in responses.items():
        if key.upper() == wildcard:
            return response
    for key, response in responses.items():
        if key.casefold() == "default":
            return response
    return None


def _schema_fields(
    schema: SchemaIR,
    *,
    selector: str = "$",
    path_segments: tuple[str, ...] = (),
    required: bool = False,
    resource_name: str | None = None,
    visited: set[int] | None = None,
) -> list[dict[str, Any]]:
    visited = set() if visited is None else set(visited)
    if id(schema) in visited:
        return []
    visited.add(id(schema))
    output: list[dict[str, Any]] = []
    current_resource_name = schema.title or resource_name
    if schema.type == "object" or schema.properties:
        for name, child in schema.properties.items():
            output.extend(
                _schema_fields(
                    child,
                    selector=f"{selector}.{name}",
                    path_segments=(*path_segments, name),
                    required=name in schema.required,
                    resource_name=current_resource_name,
                    visited=visited,
                )
            )
        return output
    if schema.type == "array" and schema.items is not None:
        return _schema_fields(
            schema.items,
            selector=f"{selector}[]",
            path_segments=path_segments,
            required=required,
            resource_name=schema.items.title or current_resource_name,
            visited=visited,
        )
    name = selector.rsplit(".", 1)[-1].removesuffix("[]")
    output.append(
        {
            "selector": selector,
            "name": name,
            "path_segments": list(path_segments),
            "type": schema.type,
            "format": schema.format,
            "description": schema.description,
            "required": required,
            "resource_name": current_resource_name,
        }
    )
    return output


def _is_json_media_type(media_type: str | None) -> bool:
    return media_type == "application/json" or bool(
        media_type and media_type.endswith("+json")
    )


def _monitor_trace_summary(
    result: APIBehaviorMonitorResult,
) -> dict[str, Any]:
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
