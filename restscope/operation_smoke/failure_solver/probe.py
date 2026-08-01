"""Current-operation scope for model-requested Failure Solve HTTP probes."""

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from typing import Any
from urllib.parse import unquote, urlsplit

from pydantic import ValidationError

from restscope.capabilities import ToolContext
from restscope.capabilities.http_request import (
    HTTP_REQUEST_TOOL_NAME,
    HTTPRequestArguments,
    HTTPRequestTimeoutError,
    HTTPRequestToolError,
    TargetHTTPRequestTool,
    http_request_tool_spec,
)
from restscope.llm import ToolCall, ToolResult, ToolSpec
from restscope.http_transport import (
    TargetOperationIdentity,
    target_operation_scope,
)
from restscope.operation_smoke.test_case_catalog import (
    CatalogTestCaseDraft,
    TestCaseCatalog,
    parse_http_failure,
    parse_transport_failure,
)
from restscope.testing import OperationGeneratorConfig

from .agent import HTTPProbe


class CurrentOperationHTTPProbe(HTTPProbe):
    """Scope the shared HTTP implementation to the current operation."""

    def __init__(
        self,
        *,
        http_tool: TargetHTTPRequestTool,
        context_provider: Callable[[], ToolContext],
    ) -> None:
        """Bind only the shared HTTP implementation and target context source."""
        self.http_tool = http_tool
        self.context_provider = context_provider

    def tool_spec(self, config: OperationGeneratorConfig) -> ToolSpec:
        """Restrict the shared HTTP tool to the operation under Investigation.

        Args:
            config: The current operation snapshot and Generator revision.

        Returns:
            A copied tool description whose method and path explain the only
            request scope available to Failure Solve. Authentication remains
            runtime-owned and is never placed in the model prompt.
        """
        source = http_request_tool_spec()
        schema = deepcopy(source.input_schema)
        schema["properties"]["method"]["enum"] = [
            config.snapshot.method.upper()
        ]
        schema["properties"]["path"]["description"] = (
            "A concrete path matching the current operation template "
            f"{config.snapshot.path}. Replace only its path parameters."
        )
        return source.model_copy(
            update={
                "description": (
                    "Probe only the current operation "
                    f"{config.snapshot.method.upper()} "
                    f"{config.snapshot.path}. Context authentication is "
                    "injected automatically."
                ),
                "input_schema": schema,
                "output_schema": {
                    "type": "object",
                    "properties": {
                        "case_id": {"type": "string", "pattern": "^TC[1-9][0-9]*$"},
                        "status_code": {"type": "integer"},
                        "failure": {},
                    },
                    "required": ["case_id", "status_code", "failure"],
                    "additionalProperties": False,
                },
            }
        )

    def execute(
        self,
        *,
        config: OperationGeneratorConfig,
        tool_call: ToolCall,
        catalog: TestCaseCatalog,
    ) -> ToolResult:
        """Validate and execute one model-requested diagnostic HTTP call.

        Args:
            config: The operation that the current Solve session investigates.
            tool_call: The model's proposed call to the shared HTTP tool.

        Returns:
            A compact result containing the newly assigned ``TC*`` reference,
            status, and parsed Failure. The full failed response remains only
            inside ``catalog``.
        """
        error = _scope_error(config, tool_call)
        if error is not None:
            return ToolResult(
                tool_call_id=tool_call.id,
                name=tool_call.name,
                status="denied",
                error={
                    "code": "operation_smoke_probe_out_of_scope",
                    "message": error,
                },
            )
        with target_operation_scope(
            TargetOperationIdentity(
                operation_key=config.operation_key,
                method=config.snapshot.method,
                path=config.snapshot.path,
            )
        ):
            raw_result = self._send(tool_call)
        return _record_probe_result(
            catalog=catalog,
            config=config,
            tool_call=tool_call,
            result=raw_result,
        )

    def _send(self, tool_call: ToolCall) -> ToolResult:
        """Translate shared HTTP implementation outcomes into a raw result."""
        try:
            payload = self.http_tool.execute(
                self.context_provider(),
                **tool_call.arguments,
            )
        except HTTPRequestTimeoutError as exc:
            return ToolResult(
                tool_call_id=tool_call.id,
                name=tool_call.name,
                status="timed_out",
                error={"code": exc.code, "message": str(exc)},
            )
        except HTTPRequestToolError as exc:
            return ToolResult(
                tool_call_id=tool_call.id,
                name=tool_call.name,
                status="failed",
                error={"code": exc.code, "message": str(exc)},
            )
        return ToolResult(
            tool_call_id=tool_call.id,
            name=tool_call.name,
            status="succeeded",
            content=payload.get("content"),
            structured=payload.get("structured"),
            artifact_ids=payload.get("artifact_ids", []),
        )

    def validate(
        self,
        *,
        config: OperationGeneratorConfig,
        tool_call: ToolCall,
    ) -> str | None:
        """Return a scope error without executing any external request."""

        return _scope_error(config, tool_call)


def _scope_error(
    config: OperationGeneratorConfig,
    tool_call: ToolCall,
) -> str | None:
    """Explain why a probe would escape the current operation, if it would.

    The method must exactly match the operation, while concrete path-parameter
    values may replace template placeholders. Absolute URLs, query strings,
    fragments, path traversal, and encoded slashes are deliberately rejected.
    """
    if tool_call.name != HTTP_REQUEST_TOOL_NAME:
        return f"{tool_call.name} is not the allowed HTTP probe tool"
    try:
        HTTPRequestArguments.model_validate(tool_call.arguments)
    except ValidationError as exc:
        issue = exc.errors(include_input=False)[0]
        location = ".".join(
            str(item) for item in issue.get("loc", ())
        ) or "request"
        return (
            f"HTTP probe field {location} is invalid: "
            f"{issue['msg']}"
        )
    method = tool_call.arguments.get("method")
    expected_method = config.snapshot.method.upper()
    if method != expected_method:
        return (
            f"HTTP probe method must be {expected_method} for the current "
            "operation"
        )
    path = tool_call.arguments.get("path")
    if not isinstance(path, str) or not _matches_path_template(
        path,
        config.snapshot.path,
    ):
        return (
            "HTTP probe path must match the current operation template "
            f"{config.snapshot.path}"
        )
    return None


def _record_probe_result(
    *,
    catalog: TestCaseCatalog,
    config: OperationGeneratorConfig,
    tool_call: ToolCall,
    result: ToolResult,
) -> ToolResult:
    """Record an attempted request and hide its full body from model feedback."""
    parameters = _probe_parameters(
        config=config,
        arguments=tool_call.arguments,
    )
    structured = result.structured if isinstance(result.structured, dict) else {}
    status_code = structured.get("status_code")
    if result.status == "succeeded" and isinstance(status_code, int):
        headers = structured.get("headers")
        headers = headers if isinstance(headers, dict) else {}
        media_type = str(headers.get("content-type") or "").split(";", 1)[0]
        body = structured.get("body") if 400 <= status_code < 600 else None
        failure = parse_http_failure(
            status_code=status_code,
            reason_phrase=str(structured.get("reason_phrase") or ""),
            media_type=media_type,
            response_body=body,
            body_truncated=False,
        )
        case = catalog.record(
            CatalogTestCaseDraft(
                parameters=parameters,
                response_body=body,
                failure=failure,
            )
        )
        return ToolResult(
            tool_call_id=tool_call.id,
            name=tool_call.name,
            status="succeeded",
            structured={
                "case_id": case.case_id,
                "status_code": status_code,
                "failure": (
                    failure.model_dump(mode="json")
                    if failure is not None
                    else None
                ),
            },
        )

    error = result.error or {}
    failure = parse_transport_failure(
        code=str(error.get("code") or error.get("type") or result.status),
        message=str(error.get("message") or "HTTP probe failed"),
    )
    case = catalog.record(
        CatalogTestCaseDraft(
            parameters=parameters,
            response_body=None,
            failure=failure,
        )
    )
    return ToolResult(
        tool_call_id=tool_call.id,
        name=tool_call.name,
        status=result.status,
        structured={
            "case_id": case.case_id,
            "failure": failure.model_dump(mode="json"),
        },
        error=result.error,
    )


def _probe_parameters(
    *,
    config: OperationGeneratorConfig,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """Translate model-visible HTTP arguments into semantic Parameter handles."""
    output: dict[str, Any] = {}
    path = str(arguments["path"])
    actual_segments = path.split("/")
    template_segments = config.snapshot.path.split("/")
    for actual, template in zip(actual_segments, template_segments):
        if template.startswith("{") and template.endswith("}"):
            name = template[1:-1]
            output[f"path.{name}"] = _typed_path_value(
                config=config,
                name=name,
                value=unquote(actual),
            )
    for name, value in (arguments.get("query") or {}).items():
        output[f"query.{name}"] = value
    for name, value in (arguments.get("headers") or {}).items():
        output[f"header.{name.lower()}"] = value
    if "json_body" in arguments:
        _flatten_body(output, "body", arguments["json_body"])
    elif "form_body" in arguments:
        _flatten_body(output, "body", arguments["form_body"])
    elif "text_body" in arguments:
        output["body"] = arguments["text_body"]
    return output


def _typed_path_value(
    *,
    config: OperationGeneratorConfig,
    name: str,
    value: str,
) -> Any:
    """Recover the OpenAPI scalar type hidden by a concrete URL path."""
    parameter = next(
        (
            item
            for item in config.snapshot.parameters
            if item.location == "path" and item.name == name
        ),
        None,
    )
    if parameter is None:
        return value
    node = next(
        (
            item
            for item in config.snapshot.input_nodes
            if item.input_node_id == parameter.input_node_id
        ),
        None,
    )
    schema_type = (
        node.schema_contract.type
        if node is not None and node.schema_contract is not None
        else None
    )
    types = set(schema_type if isinstance(schema_type, list) else [schema_type])
    try:
        if "integer" in types:
            return int(value)
        if "number" in types:
            return float(value)
        if "boolean" in types and value.casefold() in {"true", "false"}:
            return value.casefold() == "true"
    except ValueError:
        # A deliberately invalid Probe value remains the exact sent string. Its
        # Schema mismatch may itself be the evidence Solve is investigating.
        pass
    return value


def _flatten_body(
    output: dict[str, Any],
    handle: str,
    value: Any,
) -> None:
    """Retain a body value and each concrete object or array child."""
    output[handle] = value
    if isinstance(value, dict):
        for name, child in value.items():
            _flatten_body(output, f"{handle}.{name}", child)
    elif isinstance(value, list):
        # OpenAPI semantic handles use ``[]`` for an item Schema. The concrete
        # list stays intact so typed reverse lookup remains deterministic.
        output[f"{handle}[]"] = value


def _matches_path_template(path: str, template: str) -> bool:
    """Return whether a concrete safe path matches one OpenAPI path template."""

    parsed = urlsplit(path)
    if (
        parsed.scheme
        or parsed.netloc
        or parsed.query
        or parsed.fragment
        or not path.startswith("/")
        or path.startswith("//")
    ):
        return False
    actual_segments = path.split("/")
    template_segments = template.split("/")
    if len(actual_segments) != len(template_segments):
        return False
    for actual, expected in zip(actual_segments, template_segments):
        if expected.startswith("{") and expected.endswith("}"):
            decoded = unquote(actual)
            if not decoded or "/" in decoded or decoded in {".", ".."}:
                return False
            continue
        if unquote(actual) != unquote(expected):
            return False
    return True
