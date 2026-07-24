"""Translate bounded HTTP responses into resolved Resource Monitor observations."""

from __future__ import annotations

import json
from typing import Any

from restscope.http_transport import (
    TargetResponseObservation,
    TargetResponseOperationContext,
    TargetResponseProcessorWarning,
)
from restscope.openapi_parser import (
    OpenAPIOperationMatchError,
    OpenAPISpecIR,
    match_operation,
)
from restscope.openapi_parser.ir import OperationIR, SchemaIR

from .agent import ResourceMonitorAgent, ResourceMonitorOutputError
from .schemas import MonitoredOperation, ResourceObservation


MAX_RESOURCE_MONITOR_BODY_BYTES = 1024 * 1024


class ResourceMonitorResponseProcessor:
    """Resolve operation/schema context outside the Agent, then observe."""

    def __init__(self, agent: ResourceMonitorAgent) -> None:
        self.agent = agent

    def process(
        self,
        observation: TargetResponseObservation,
        context: TargetResponseOperationContext,
    ) -> TargetResponseProcessorWarning | None:
        if not 200 <= observation.status_code < 300:
            return None
        if not isinstance(context.ir, OpenAPISpecIR):
            return _warning(
                "resource_monitor_context_invalid",
                "Resource monitoring requires an initialized OpenAPI context",
            )
        try:
            operation, operation_ir = _resolve_operation(
                observation,
                context=context,
                ir=context.ir,
            )
        except OpenAPIOperationMatchError as exc:
            return TargetResponseProcessorWarning(
                code=exc.code,
                message=str(exc),
                issues=exc.operation_keys[:20],
            )
        if (
            observation.body_truncated
            or len(observation.body) > MAX_RESOURCE_MONITOR_BODY_BYTES
        ):
            result = self.agent.observe(
                ResourceObservation(
                    operation=operation,
                    status_code=observation.status_code,
                    media_type=_media_type(observation.headers),
                    body=None,
                    body_truncated=True,
                )
            )
            return _result_warning(result.warning)

        media_type = _media_type(observation.headers)
        if not _is_json_media_type(media_type):
            return self._record_operation_warning(
                operation,
                code="resource_monitor_non_json",
                message="A 2xx response was not JSON and was not monitored",
            )
        try:
            body = json.loads(observation.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return self._record_operation_warning(
                operation,
                code="resource_monitor_invalid_json",
                message="A 2xx JSON response could not be decoded for monitoring",
            )
        try:
            result = self.agent.observe(
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
        except ResourceMonitorOutputError as exc:
            return self._record_operation_warning(
                operation,
                code=exc.code,
                message=str(exc),
            )
        except Exception as exc:
            return self._record_operation_warning(
                operation,
                code="resource_monitor_failed",
                message="Response resource monitoring failed",
                issues=(type(exc).__name__,),
            )
        return _result_warning(result.warning)

    def _record_operation_warning(
        self,
        operation: MonitoredOperation,
        *,
        code: str,
        message: str,
        issues: tuple[str, ...] = (),
    ) -> TargetResponseProcessorWarning:
        from .schemas import ResourceMonitorWarning

        warning = ResourceMonitorWarning(
            code=code,
            message=message,
            issues=list(issues),
        )
        self.agent.catalog.record_operation_error(
            operation=operation,
            warning=warning,
        )
        return _result_warning(warning)


def _resolve_operation(
    observation: TargetResponseObservation,
    *,
    context: TargetResponseOperationContext,
    ir: OpenAPISpecIR,
) -> tuple[MonitoredOperation, OperationIR | None]:
    if context.operation_key is not None:
        operation_ir = ir.operations.get(context.operation_key)
        method = (
            context.operation_method
            or (operation_ir.method if operation_ir is not None else observation.method)
        )
        path = (
            context.operation_path
            or (operation_ir.path if operation_ir is not None else observation.path)
        )
        return (
            MonitoredOperation(
                operation_key=context.operation_key,
                method=method,
                path=path,
            ),
            operation_ir,
        )
    operation_ir = match_operation(
        ir,
        method=observation.method,
        path=observation.path,
    )
    return (
        MonitoredOperation(
            operation_key=operation_ir.operation_key,
            method=operation_ir.method,
            path=operation_ir.path,
        ),
        operation_ir,
    )


def _response_schema_fields(
    operation: OperationIR | None,
    *,
    status_code: int,
    media_type: str | None,
) -> list[dict[str, Any]]:
    if operation is None:
        return []
    response = _response_for_status(operation, status_code)
    if response is None:
        return []
    media = None
    if media_type is not None:
        media = response.contents.get(media_type)
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
    visited: set[int] | None = None,
) -> list[dict[str, Any]]:
    visited = set() if visited is None else set(visited)
    if id(schema) in visited:
        return []
    visited.add(id(schema))
    output: list[dict[str, Any]] = []
    if schema.type == "object" or schema.properties:
        for name, child in schema.properties.items():
            output.extend(
                _schema_fields(
                    child,
                    selector=f"{selector}.{name}",
                    path_segments=(*path_segments, name),
                    required=name in schema.required,
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
        }
    )
    return output


def _media_type(headers) -> str | None:
    value = headers.get("content-type", "")
    normalized = value.split(";", 1)[0].strip().casefold()
    return normalized or None


def _is_json_media_type(media_type: str | None) -> bool:
    return bool(
        media_type
        and (
            media_type == "application/json"
            or media_type.endswith("+json")
        )
    )


def _result_warning(warning) -> TargetResponseProcessorWarning | None:
    if warning is None:
        return None
    return TargetResponseProcessorWarning(
        code=warning.code,
        message=warning.message,
        issues=tuple(warning.issues),
    )


def _warning(code: str, message: str) -> TargetResponseProcessorWarning:
    return TargetResponseProcessorWarning(code=code, message=message)
