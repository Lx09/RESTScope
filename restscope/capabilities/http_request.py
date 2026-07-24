"""App-bound HTTP request capability for the initialized target."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from typing import Any, Literal
from urllib.parse import urlencode

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from restscope.capabilities.tool_context import ToolContext
from restscope.capabilities.tool_registry import ToolRegistry
from restscope.llm.schemas import ToolSpec
from restscope.http_transport import (
    BufferedTargetResponse,
    TargetHTTPTimeout,
    TargetHTTPTransport,
    TargetHTTPTransportError,
    TargetResponseOperationContext,
)


HTTP_REQUEST_TOOL_NAME = "restscope.http.request"
MAX_RESPONSE_BYTES = 10 * 1024 * 1024
HTTPMethod = Literal["GET", "HEAD", "OPTIONS", "POST", "PUT", "PATCH", "DELETE"]
ParameterScalar = str | int | float | bool
ParameterValue = ParameterScalar | list[ParameterScalar] | None
ClientFactory = Callable[..., httpx.Client]

_BODY_FIELDS = {"json_body", "text_body", "form_body"}
_TEXT_MEDIA_TYPES = {
    "application/graphql",
    "application/javascript",
    "application/x-www-form-urlencoded",
    "application/x-yaml",
    "application/xml",
    "application/yaml",
}


class HTTPRequestToolError(RuntimeError):
    """Stable tool error that does not expose request credentials."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class HTTPRequestTimeoutError(TimeoutError):
    """Stable timeout error consumed by ToolExecutor."""

    code = "request_timeout"


class HTTPRequestArguments(BaseModel):
    """Strict model for one target-bound HTTP request."""

    model_config = ConfigDict(extra="forbid")

    method: HTTPMethod
    path: str
    query: dict[str, ParameterValue] = Field(default_factory=dict)
    headers: dict[str, str] = Field(default_factory=dict)
    json_body: Any | None = None
    text_body: str | None = None
    form_body: dict[str, ParameterValue] | None = None
    timeout_seconds: float = Field(default=30, gt=0, le=30)

    @model_validator(mode="after")
    def require_one_body_encoding(self) -> "HTTPRequestArguments":
        supplied = self.model_fields_set.intersection(_BODY_FIELDS)
        if len(supplied) > 1:
            raise ValueError("json_body, text_body, and form_body are mutually exclusive")
        return self


def register_http_request_tool(
    registry: ToolRegistry,
    *,
    client_factory: ClientFactory = httpx.Client,
    transport: TargetHTTPTransport | None = None,
) -> ToolSpec:
    """Register the model-visible target HTTP request contract."""

    spec = ToolSpec(
        name=HTTP_REQUEST_TOOL_NAME,
        description=(
            "Send one HTTP request to the target bound to this RESTScope App. "
            "The path must be relative to the configured base URL."
        ),
        kind="local_function",
        input_schema={
            "type": "object",
            "properties": {
                "method": {
                    "type": "string",
                    "enum": ["GET", "HEAD", "OPTIONS", "POST", "PUT", "PATCH", "DELETE"],
                },
                "path": {"type": "string", "pattern": "^/(?!/)"},
                "query": {"type": "object"},
                "headers": {
                    "type": "object",
                    "additionalProperties": {"type": "string"},
                },
                "json_body": {},
                "text_body": {"type": "string"},
                "form_body": {"type": "object"},
                "timeout_seconds": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "maximum": 30,
                    "default": 30,
                },
            },
            "required": ["method", "path"],
            "additionalProperties": False,
            "allOf": [
                {"not": {"required": ["json_body", "text_body"]}},
                {"not": {"required": ["json_body", "form_body"]}},
                {"not": {"required": ["text_body", "form_body"]}},
            ],
        },
        output_schema={
            "type": "object",
            "properties": {
                "status_code": {"type": "integer"},
                "reason_phrase": {"type": "string"},
                "url": {"type": "string"},
                "headers": {"type": "object"},
                "body_format": {"type": "string", "enum": ["json", "text"]},
                "body": {},
                "size_bytes": {"type": "integer"},
                "resource_monitor_warning": {
                    "type": "object",
                    "properties": {
                        "code": {"type": "string"},
                        "message": {"type": "string"},
                        "issues": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                    },
                    "required": ["code", "message", "issues"],
                    "additionalProperties": False,
                },
            },
            "required": [
                "status_code",
                "reason_phrase",
                "url",
                "headers",
                "body_format",
                "body",
                "size_bytes",
            ],
        },
        risk_level="high",
        read_only=False,
        requires_approval=False,
        timeout_seconds=30,
        metadata={"target_bound": True, "open_world": True},
    )
    registry.register(
        spec=spec,
        handler=TargetHTTPRequestTool(
            client_factory=client_factory,
            transport=transport,
        ).execute,
    )
    return spec


class TargetHTTPRequestTool:
    """Send one bounded request to the App's configured target."""

    def __init__(
        self,
        *,
        client_factory: ClientFactory = httpx.Client,
        transport: TargetHTTPTransport | None = None,
    ) -> None:
        self.transport = transport or TargetHTTPTransport(
            client_factory=client_factory
        )

    def execute(self, context: ToolContext, /, **arguments: Any) -> dict[str, Any]:
        request = _validate_arguments(arguments)
        request_kwargs = _body_arguments(request)
        request_headers = dict(request.headers)
        if "form_body" in request.model_fields_set and not _contains_header(request_headers, "content-type"):
            request_headers["Content-Type"] = "application/x-www-form-urlencoded"

        try:
            prepared = self.transport.prepare(
                method=request.method,
                base_url=context.base_url,
                path=request.path,
                query_items=_parameter_items(request.query),
                context_headers=context.headers,
                request_headers=request_headers,
                override_context_headers=True,
            )
            response = self.transport.request_prepared(
                prepared,
                timeout_seconds=request.timeout_seconds,
                request_kwargs=request_kwargs,
                response_body_limit=MAX_RESPONSE_BYTES,
                truncate_response_body=False,
                processor_context=TargetResponseOperationContext(ir=context.ir),
            )
            payload = _response_payload(response)
        except TargetHTTPTimeout as exc:
            raise HTTPRequestTimeoutError("HTTP request timed out") from exc
        except TargetHTTPTransportError as exc:
            raise HTTPRequestToolError(
                exc.code,
                str(exc),
            ) from exc

        return {
            "content": (
                f"HTTP {payload['status_code']} {request.method} {request.path} "
                f"({payload['size_bytes']} bytes)"
            ),
            "structured": payload,
        }


def _validate_arguments(arguments: Mapping[str, Any]) -> HTTPRequestArguments:
    try:
        return HTTPRequestArguments.model_validate(arguments)
    except ValidationError as exc:
        issue = exc.errors(include_input=False)[0]
        location = ".".join(str(item) for item in issue.get("loc", ())) or "request"
        raise HTTPRequestToolError(
            "invalid_request",
            f"Invalid HTTP request field {location}: {issue['msg']}",
        ) from exc


def _parameter_items(values: Mapping[str, ParameterValue]) -> list[tuple[str, str]]:
    items: list[tuple[str, str]] = []
    for key, value in values.items():
        if value is None:
            continue
        entries = value if isinstance(value, list) else [value]
        items.extend((key, _parameter_text(item)) for item in entries)
    return items


def _parameter_text(value: ParameterScalar) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _body_arguments(request: HTTPRequestArguments) -> dict[str, Any]:
    supplied = request.model_fields_set.intersection(_BODY_FIELDS)
    if "json_body" in supplied:
        try:
            json.dumps(request.json_body, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise HTTPRequestToolError(
                "invalid_request",
                "Invalid HTTP request field json_body: value is not JSON serializable",
            ) from exc
        return {"json": request.json_body}
    if "text_body" in supplied:
        return {"content": request.text_body}
    if "form_body" in supplied:
        return {"content": urlencode(_parameter_items(request.form_body or {}))}
    return {}


def _contains_header(headers: Mapping[str, str], expected: str) -> bool:
    return any(name.lower() == expected for name in headers)


def _response_payload(
    response: BufferedTargetResponse,
) -> dict[str, Any]:
    assert response.body is not None
    content = response.body
    media_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    body_format, body = _decode_response(response, content=content, media_type=media_type)
    payload = {
        "status_code": response.status_code,
        "reason_phrase": response.reason_phrase,
        "url": response.url,
        "headers": dict(response.headers),
        "body_format": body_format,
        "body": body,
        "size_bytes": len(content),
    }
    if response.processor_warning is not None:
        payload["resource_monitor_warning"] = {
            "code": response.processor_warning.code,
            "message": response.processor_warning.message,
            "issues": list(response.processor_warning.issues),
        }
    return payload


def _decode_response(
    response: BufferedTargetResponse,
    *,
    content: bytes,
    media_type: str,
) -> tuple[Literal["json", "text"], Any]:
    if media_type == "application/json" or media_type.endswith("+json"):
        try:
            return "json", json.loads(content.decode(response.encoding or "utf-8"))
        except (LookupError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise HTTPRequestToolError(
                "invalid_json_response",
                "HTTP response declared JSON but could not be decoded",
            ) from exc
    if not media_type and b"\x00" in content:
        raise HTTPRequestToolError(
            "unsupported_response_media_type",
            "HTTP response body is not decodable text",
        )
    textual = (
        not media_type
        or media_type.startswith("text/")
        or media_type in _TEXT_MEDIA_TYPES
        or media_type.endswith("+xml")
    )
    if not textual:
        raise HTTPRequestToolError(
            "unsupported_response_media_type",
            f"HTTP response media type is not JSON or text: {media_type or 'unknown'}",
        )
    try:
        return "text", content.decode(response.encoding or "utf-8")
    except (LookupError, UnicodeDecodeError) as exc:
        raise HTTPRequestToolError(
            "unsupported_response_media_type",
            "HTTP response body is not decodable text",
        ) from exc
