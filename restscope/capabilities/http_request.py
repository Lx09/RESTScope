"""App-bound HTTP request capability for the initialized target."""

from __future__ import annotations

import json
import re

from collections.abc import Callable, Iterable, Mapping
from typing import Any, Literal
from urllib.parse import unquote, urlencode, urlsplit, urlunsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from restscope.capabilities.tool_context import ToolContext
from restscope.capabilities.tool_registry import ToolRegistry
from restscope.llm.redactor import Redactor
from restscope.llm.schemas import ToolSpec


HTTP_REQUEST_TOOL_NAME = "restscope.http.request"
MAX_RESPONSE_BYTES = 10 * 1024 * 1024
HTTPMethod = Literal["GET", "HEAD", "OPTIONS", "POST", "PUT", "PATCH", "DELETE"]
ParameterScalar = str | int | float | bool
ParameterValue = ParameterScalar | list[ParameterScalar] | None
ClientFactory = Callable[..., httpx.Client]

_BODY_FIELDS = {"json_body", "text_body", "form_body"}
_HOP_BY_HOP_HEADERS = {
    "connection",
    "content-length",
    "host",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}
_PRIVATE_RESPONSE_HEADERS = {
    "authorization",
    "cookie",
    "proxy-authenticate",
    "proxy-authorization",
    "set-cookie",
    "www-authenticate",
}
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
        handler=TargetHTTPRequestTool(client_factory=client_factory).execute,
    )
    return spec


class TargetHTTPRequestTool:
    """Send one bounded request to the App's configured target."""

    def __init__(self, *, client_factory: ClientFactory = httpx.Client) -> None:
        self.client_factory = client_factory

    def execute(self, context: ToolContext, /, **arguments: Any) -> dict[str, Any]:
        request = _validate_arguments(arguments)
        url = _target_url(context.base_url, request.path, request.query)
        headers = _request_headers(context.headers, request.headers)
        request_kwargs = _body_arguments(request)
        if "form_body" in request.model_fields_set and not _contains_header(headers, "content-type"):
            headers["Content-Type"] = "application/x-www-form-urlencoded"

        try:
            with self.client_factory(
                timeout=request.timeout_seconds,
                follow_redirects=False,
            ) as client:
                with client.stream(
                    request.method,
                    url,
                    headers=headers,
                    **request_kwargs,
                ) as response:
                    content = _read_response(response)
                    payload = _response_payload(
                        response,
                        content=content,
                        secret_values=context.headers.values(),
                    )
        except httpx.TimeoutException as exc:
            raise HTTPRequestTimeoutError("HTTP request timed out") from exc
        except httpx.HTTPError as exc:
            raise HTTPRequestToolError(
                "request_failed",
                f"HTTP request failed ({type(exc).__name__})",
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


def _target_url(
    base_url: str | None,
    path: str,
    query: Mapping[str, ParameterValue],
) -> httpx.URL:
    if not base_url:
        raise HTTPRequestToolError(
            "target_base_url_not_configured",
            "The App target base URL is not configured",
        )
    parsed = urlsplit(base_url)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise HTTPRequestToolError("invalid_base_url", "The App target base URL is invalid")
    _validate_relative_path(path)
    base_path = parsed.path.rstrip("/")
    url = httpx.URL(urlunsplit((parsed.scheme, parsed.netloc, f"{base_path}{path}", "", "")))
    params = _parameter_items(query)
    return url.copy_with(params=params) if params else url


def _validate_relative_path(path: str) -> None:
    if not path.startswith("/") or path.startswith("//") or "?" in path or "#" in path or "\\" in path:
        raise HTTPRequestToolError(
            "invalid_path",
            "HTTP request path must be a single-slash relative target path",
        )
    decoded = path
    for _ in range(3):
        expanded = unquote(decoded)
        if expanded == decoded:
            break
        decoded = expanded
    if any(segment in {".", ".."} for segment in decoded.split("/")):
        raise HTTPRequestToolError("invalid_path", "HTTP request path cannot contain dot segments")


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


def _request_headers(
    context_headers: Mapping[str, str],
    call_headers: Mapping[str, str],
) -> dict[str, str]:
    merged = dict(context_headers)
    existing = {name.lower(): name for name in merged}
    for name, value in call_headers.items():
        normalized = name.strip().lower()
        if _is_sensitive_header(normalized) or normalized in _HOP_BY_HOP_HEADERS:
            raise HTTPRequestToolError(
                "forbidden_header",
                f"HTTP request header cannot be set per call: {name}",
            )
        previous = existing.get(normalized)
        if previous is not None:
            merged.pop(previous)
        merged[name] = value
        existing[normalized] = name
    return merged


def _is_sensitive_header(name: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    return (
        "authorization" in normalized
        or "cookie" in normalized
        or "api_key" in normalized
        or "token" in normalized
        or "secret" in normalized
        or normalized == "auth"
        or normalized.endswith("_auth")
    )


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


def _read_response(response: httpx.Response) -> bytes:
    content = bytearray()
    for chunk in response.iter_bytes():
        if len(content) + len(chunk) > MAX_RESPONSE_BYTES:
            raise HTTPRequestToolError(
                "response_too_large",
                f"HTTP response exceeds the {MAX_RESPONSE_BYTES}-byte limit",
            )
        content.extend(chunk)
    return bytes(content)


def _response_payload(
    response: httpx.Response,
    *,
    content: bytes,
    secret_values: Iterable[str],
) -> dict[str, Any]:
    media_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    body_format, body = _decode_response(response, content=content, media_type=media_type)
    redactor = _ResponseRedactor(secret_values)
    return {
        "status_code": response.status_code,
        "reason_phrase": response.reason_phrase,
        "url": redactor.url(response.url),
        "headers": _response_headers(response.headers, redactor),
        "body_format": body_format,
        "body": redactor.value(body),
        "size_bytes": len(content),
    }


def _decode_response(
    response: httpx.Response,
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


def _response_headers(headers: httpx.Headers, redactor: "_ResponseRedactor") -> dict[str, str]:
    output: dict[str, str] = {}
    for name, value in headers.items():
        normalized = name.lower()
        if normalized in _PRIVATE_RESPONSE_HEADERS or _is_sensitive_header(normalized):
            continue
        output[normalized] = redactor.text(value)
    return output


class _ResponseRedactor:
    def __init__(self, secret_values: Iterable[str]) -> None:
        self.redactor = Redactor()
        self.secret_values = sorted(
            {value for value in secret_values if value},
            key=len,
            reverse=True,
        )

    def value(self, value: Any) -> Any:
        if isinstance(value, Mapping):
            output: dict[str, Any] = {}
            for key, item in value.items():
                normalized = str(key).strip().lower().replace("-", "_")
                output[str(key)] = (
                    "***REDACTED***"
                    if normalized in self.redactor.SECRET_KEYS or _is_sensitive_header(normalized)
                    else self.value(item)
                )
            return output
        if isinstance(value, list | tuple):
            return [self.value(item) for item in value]
        if isinstance(value, str):
            return self.text(value)
        return value

    def text(self, value: str) -> str:
        redacted = self.redactor.redact_text(value)
        for secret in self.secret_values:
            redacted = redacted.replace(secret, "***REDACTED***")
        return redacted

    def url(self, value: httpx.URL) -> str:
        params: list[tuple[str, str]] = []
        for key, item in value.params.multi_items():
            normalized = key.strip().lower().replace("-", "_")
            params.append(
                (
                    key,
                    "***REDACTED***"
                    if normalized in self.redactor.SECRET_KEYS or _is_sensitive_header(normalized)
                    else self.text(item),
                )
            )
        sanitized = value.copy_with(params=params) if params else value
        return self.text(str(sanitized))
