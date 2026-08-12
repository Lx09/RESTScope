"""App-bound HTTP request capability for the initialized target."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from typing import Literal
from urllib.parse import urlencode

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from restscope.target_http.request import is_json_media_type, normalize_media_type
from restscope.tools.context import ToolContext
from restscope.llm.schemas import ToolSpec
from restscope.target_http import (
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

_PARAMETER_VALUE_SCHEMA = {
    # ``integer`` is also a valid JSON Schema ``number``. ``anyOf`` therefore
    # accepts integer query/form values without creating an accidental overlap
    # rejection at the model boundary.
    "anyOf": [
        {"type": "string"},
        {"type": "integer"},
        {"type": "number"},
        {"type": "boolean"},
        {"type": "null"},
        {
            "type": "array",
            "items": {
                "anyOf": [
                    {"type": "string"},
                    {"type": "integer"},
                    {"type": "number"},
                    {"type": "boolean"},
                ]
            },
        },
    ]
}

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
    """Stable timeout error consumed by a Harness-owned HTTP seam."""

    code = "request_timeout"


class HTTPRequestArguments(BaseModel):
    """Strict model for model-visible parts of one target-bound HTTP request.

    The target host, App authentication headers, OpenAPI IR, and scoped
    operation identity are intentionally absent. A model can choose only the
    relative request data declared here.
    """

    model_config = ConfigDict(extra="forbid")

    method: HTTPMethod
    path: str
    query: dict[str, ParameterValue] = Field(default_factory=dict)
    headers: dict[str, str] = Field(default_factory=dict)
    json_body: object | None = None
    text_body: str | None = None
    form_body: dict[str, ParameterValue] | None = None
    timeout_seconds: float = Field(default=30, gt=0, le=30)

    @model_validator(mode="after")
    def require_one_body_encoding(self) -> "HTTPRequestArguments":
        """Reject ambiguous requests that supply more than one body encoding."""
        supplied = self.model_fields_set.intersection(_BODY_FIELDS)
        if len(supplied) > 1:
            raise ValueError("json_body, text_body, and form_body are mutually exclusive")
        return self


def http_request_tool_spec() -> ToolSpec:
    """Return the one canonical contract for an ordinary target request."""
    return ToolSpec(
        name=HTTP_REQUEST_TOOL_NAME,
        description=(
            "Send one HTTP request to the target bound to this RESTScope App. "
            "The path must be relative to the configured base URL. Write methods "
            "may permanently change target state and are not rolled back."
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
                "query": {
                    "type": "object",
                    "description": (
                        "Query names mapped to a scalar, repeated scalar values, "
                        "or null. The Harness encodes these values into the URL."
                    ),
                    "additionalProperties": _PARAMETER_VALUE_SCHEMA,
                },
                "headers": {
                    "type": "object",
                    "additionalProperties": {"type": "string"},
                },
                "json_body": {
                    "description": (
                        "A JSON value used as the request body. This field is "
                        "intentionally open because OpenAPI bodies may be scalar, "
                        "array, object, boolean, or null."
                    )
                },
                "text_body": {"type": "string"},
                "form_body": {
                    "type": "object",
                    "description": (
                        "Form field names mapped to a scalar, repeated scalar "
                        "values, or null. Mutually exclusive with other bodies."
                    ),
                    "additionalProperties": _PARAMETER_VALUE_SCHEMA,
                },
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
                "headers": {
                    "type": "object",
                    "description": "Response header names mapped to bounded text values.",
                    "additionalProperties": {"type": "string"},
                },
                "body_format": {"type": "string", "enum": ["json", "text"]},
                "body": {
                    "description": (
                        "Parsed JSON or bounded response text, as identified by "
                        "body_format. The value is intentionally open."
                    )
                },
                "size_bytes": {"type": "integer"},
                "response_validation": {
                    "type": "string",
                    "enum": ["evaluated", "partial", "not_evaluated"],
                },
                "behavior_monitor_warnings": {
                    "type": "array",
                    "items": {
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
            },
            "required": [
                "status_code",
                "reason_phrase",
                "url",
                "headers",
                "body_format",
                "body",
                "size_bytes",
                "response_validation",
                "behavior_monitor_warnings",
            ],
            "additionalProperties": False,
        },
    )


class TargetHTTPRequestTool:
    """Send one bounded request to the App's configured target.

    Arguments are validated twice: Pydantic checks their data shape, then the
    shared transport checks URL/header security. Behavior monitoring matches
    the concrete method and path against the current OpenAPI document.
    """

    def __init__(
        self,
        *,
        client_factory: ClientFactory = httpx.Client,
        transport: TargetHTTPTransport | None = None,
    ) -> None:
        self.transport = transport or TargetHTTPTransport(
            client_factory=client_factory
        )

    def execute(self, context: ToolContext, /, **arguments: object) -> dict[str, object]:
        """Validate, send, monitor, decode, and summarize one HTTP tool call."""
        request = _validate_arguments(arguments)
        request_kwargs = _body_arguments(request)
        request_headers = dict(request.headers)
        if (
            "form_body" in request.model_fields_set
            and not _contains_header(request_headers, "content-type")
        ):
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
                allowed_sensitive_request_headers=(
                    set()
                ),
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


def _validate_arguments(arguments: Mapping[str, object]) -> HTTPRequestArguments:
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


def _body_arguments(request: HTTPRequestArguments) -> dict[str, object]:
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
) -> dict[str, object]:
    """Convert a buffered transport response into the tool's public JSON result."""
    assert response.body is not None
    content = response.body
    media_type = normalize_media_type(response.headers.get("content-type")) or ""
    body_format, body = _decode_response(response, content=content, media_type=media_type)
    payload = {
        "status_code": response.status_code,
        "reason_phrase": response.reason_phrase,
        "url": response.url,
        "headers": dict(response.headers),
        "body_format": body_format,
        "body": body,
        "size_bytes": len(content),
        "response_validation": (
            response.processor_result.response_validation
            if response.processor_result is not None
            else "not_evaluated"
        ),
        "behavior_monitor_warnings": [
            {
                "code": warning.code,
                "message": warning.message,
                "issues": list(warning.issues),
            }
            for warning in (
                response.processor_result.warnings
                if response.processor_result is not None
                else ()
            )
        ],
    }
    return payload


def _decode_response(
    response: BufferedTargetResponse,
    *,
    content: bytes,
    media_type: str,
) -> tuple[Literal["json", "text"], object]:
    """Decode only declared JSON or safely recognizable text response bodies.

    Binary data is rejected rather than embedded in model context. A response
    declaring JSON must actually decode as JSON; silently falling back to text
    would hide a target contract failure.
    """
    if is_json_media_type(media_type):
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
