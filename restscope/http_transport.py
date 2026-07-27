"""Shared target-bound HTTP transport primitives."""

from __future__ import annotations

from collections.abc import Callable, Collection, Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import re
from typing import Any, Literal, Protocol
from urllib.parse import unquote, urlsplit, urlunsplit
from urllib.parse import quote

import httpx


ClientFactory = Callable[..., httpx.Client]
QueryItem = tuple[str, str] | tuple[str, str, bool]

HOP_BY_HOP_HEADERS = {
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


@dataclass(slots=True, frozen=True)
class PreparedTargetRequest:
    """A fully target-validated request without an opened HTTP client."""

    method: str
    path: str
    url: httpx.URL
    headers: dict[str, str]


@dataclass(slots=True, frozen=True)
class TargetResponseOperationContext:
    """OpenAPI context attached by a caller without coupling transport to an Agent."""

    ir: object
    operation_key: str | None = None
    operation_method: str | None = None
    operation_path: str | None = None


@dataclass(slots=True, frozen=True)
class TargetOperationIdentity:
    """Exact operation identity bound around one internal HTTP invocation."""

    operation_key: str
    method: str
    path: str


_TARGET_OPERATION_IDENTITY: ContextVar[TargetOperationIdentity | None] = (
    ContextVar("restscope_target_operation_identity", default=None)
)


@contextmanager
def target_operation_scope(
    identity: TargetOperationIdentity,
) -> Iterator[None]:
    """Bind an exact operation without adding it to model-visible arguments."""

    token = _TARGET_OPERATION_IDENTITY.set(identity)
    try:
        yield
    finally:
        _TARGET_OPERATION_IDENTITY.reset(token)


def current_target_operation_identity() -> TargetOperationIdentity | None:
    return _TARGET_OPERATION_IDENTITY.get()


@dataclass(slots=True, frozen=True)
class TargetResponseObservation:
    """Bounded response evidence offered to one synchronous processor."""

    method: str
    path: str
    url: str
    status_code: int
    reason_phrase: str
    headers: Mapping[str, str]
    body: bytes
    body_truncated: bool


@dataclass(slots=True, frozen=True)
class TargetResponseProcessorWarning:
    """A processor failure that must not replace the original HTTP result."""

    code: str
    message: str
    issues: tuple[str, ...] = ()


@dataclass(slots=True, frozen=True)
class TargetResponseProcessorResult:
    """Generic structured outcome from one synchronous response processor."""

    response_validation: Literal["evaluated", "partial", "not_evaluated"]
    warnings: tuple[TargetResponseProcessorWarning, ...] = ()
    details: Mapping[str, Any] | None = None


class TargetResponseProcessor(Protocol):
    def process(
        self,
        observation: TargetResponseObservation,
        context: TargetResponseOperationContext,
    ) -> TargetResponseProcessorResult | TargetResponseProcessorWarning | None: ...


@dataclass(slots=True, frozen=True)
class BufferedTargetResponse:
    """Response metadata plus an optional bounded body after the stream closes."""

    status_code: int
    reason_phrase: str
    url: str
    headers: Mapping[str, str]
    encoding: str | None
    body: bytes | None
    body_truncated: bool
    processor_result: TargetResponseProcessorResult | None


class TargetHTTPTransportError(RuntimeError):
    """Stable target transport error without remote exception details."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class TargetHTTPTimeout(TimeoutError):
    """One target request exceeded its configured timeout."""


class TargetHTTPTransport:
    """Open isolated response streams against one App-bound target."""

    def __init__(
        self,
        *,
        client_factory: ClientFactory = httpx.Client,
        response_processor: TargetResponseProcessor | None = None,
    ) -> None:
        self.client_factory = client_factory
        self.response_processor = response_processor

    @property
    def has_response_processor(self) -> bool:
        return self.response_processor is not None

    def prepare(
        self,
        *,
        method: str,
        base_url: str | None,
        path: str,
        query_items: Sequence[QueryItem] = (),
        context_headers: Mapping[str, str] | None = None,
        request_headers: Mapping[str, str] | None = None,
        override_context_headers: bool = True,
        allowed_sensitive_request_headers: Collection[str] = (),
    ) -> PreparedTargetRequest:
        """Validate and resolve a request without opening a client or socket."""

        return PreparedTargetRequest(
            method=method,
            path=path,
            url=build_target_url(base_url, path, query_items),
            headers=merge_target_headers(
                context_headers or {},
                request_headers or {},
                override_context_headers=override_context_headers,
                allowed_sensitive_request_headers=allowed_sensitive_request_headers,
            ),
        )

    @contextmanager
    def stream(
        self,
        *,
        method: str,
        base_url: str | None,
        path: str,
        query_items: Sequence[QueryItem] = (),
        context_headers: Mapping[str, str] | None = None,
        request_headers: Mapping[str, str] | None = None,
        override_context_headers: bool = True,
        allowed_sensitive_request_headers: Collection[str] = (),
        timeout_seconds: float = 30,
        request_kwargs: Mapping[str, Any] | None = None,
    ) -> Iterator[httpx.Response]:
        prepared = self.prepare(
            method=method,
            base_url=base_url,
            path=path,
            query_items=query_items,
            context_headers=context_headers,
            request_headers=request_headers,
            override_context_headers=override_context_headers,
            allowed_sensitive_request_headers=allowed_sensitive_request_headers,
        )
        with self.stream_prepared(
            prepared,
            timeout_seconds=timeout_seconds,
            request_kwargs=request_kwargs,
        ) as response:
            yield response

    @contextmanager
    def stream_prepared(
        self,
        prepared: PreparedTargetRequest,
        *,
        timeout_seconds: float = 30,
        request_kwargs: Mapping[str, Any] | None = None,
    ) -> Iterator[httpx.Response]:
        """Execute a request previously accepted by :meth:`prepare`."""

        try:
            with self.client_factory(timeout=timeout_seconds, follow_redirects=False) as client:
                with client.stream(
                    prepared.method,
                    prepared.url,
                    headers=prepared.headers,
                    **dict(request_kwargs or {}),
                ) as response:
                    yield response
        except httpx.TimeoutException as exc:
            raise TargetHTTPTimeout("HTTP request timed out") from exc
        except httpx.HTTPError as exc:
            raise TargetHTTPTransportError(
                "request_failed",
                f"HTTP request failed ({type(exc).__name__})",
            ) from exc

    def request_prepared(
        self,
        prepared: PreparedTargetRequest,
        *,
        timeout_seconds: float = 30,
        request_kwargs: Mapping[str, Any] | None = None,
        response_body_limit: int | None = None,
        failure_response_body_limit: int | None = None,
        truncate_response_body: bool = False,
        buffer_success_body_only: bool = False,
        processor_context: TargetResponseOperationContext | None = None,
    ) -> BufferedTargetResponse:
        """Execute, optionally buffer, and synchronously process one response."""

        with self.stream_prepared(
            prepared,
            timeout_seconds=timeout_seconds,
            request_kwargs=request_kwargs,
        ) as response:
            body: bytes | None = None
            body_truncated = False
            successful = 200 <= response.status_code < 300
            selected_body_limit = (
                failure_response_body_limit
                if not successful and failure_response_body_limit is not None
                else response_body_limit
                if response_body_limit is not None
                and (not buffer_success_body_only or successful)
                else None
            )
            if selected_body_limit is not None:
                body, body_truncated = _read_bounded_response(
                    response,
                    limit=selected_body_limit,
                    truncate=truncate_response_body,
                )
            processor_result: TargetResponseProcessorResult | None = None
            if (
                self.response_processor is not None
                and processor_context is not None
                and body is not None
            ):
                observation = TargetResponseObservation(
                    method=prepared.method,
                    path=prepared.path,
                    url=str(response.url),
                    status_code=response.status_code,
                    reason_phrase=response.reason_phrase,
                    headers={
                        name.lower(): value
                        for name, value in response.headers.items()
                    },
                    body=body,
                    body_truncated=body_truncated,
                )
                try:
                    raw_processor_result = self.response_processor.process(
                        observation,
                        processor_context,
                    )
                    if isinstance(
                        raw_processor_result,
                        TargetResponseProcessorResult,
                    ):
                        processor_result = raw_processor_result
                    elif isinstance(
                        raw_processor_result,
                        TargetResponseProcessorWarning,
                    ):
                        processor_result = TargetResponseProcessorResult(
                            response_validation="partial",
                            warnings=(raw_processor_result,),
                        )
                    else:
                        processor_result = TargetResponseProcessorResult(
                            response_validation="not_evaluated",
                        )
                except Exception as exc:
                    processor_result = TargetResponseProcessorResult(
                        response_validation="partial",
                        warnings=(
                            TargetResponseProcessorWarning(
                                code="api_behavior_monitor_failed",
                                message="API behavior monitoring failed",
                                issues=(type(exc).__name__,),
                            ),
                        ),
                    )
            return BufferedTargetResponse(
                status_code=response.status_code,
                reason_phrase=response.reason_phrase,
                url=str(response.url),
                headers={
                    name.lower(): value
                    for name, value in response.headers.items()
                },
                encoding=response.encoding,
                body=body,
                body_truncated=body_truncated,
                processor_result=processor_result,
            )


def _read_bounded_response(
    response: httpx.Response,
    *,
    limit: int,
    truncate: bool,
) -> tuple[bytes, bool]:
    content = bytearray()
    for chunk in response.iter_bytes():
        remaining = limit - len(content)
        if len(chunk) > remaining:
            if not truncate:
                raise TargetHTTPTransportError(
                    "response_too_large",
                    f"HTTP response exceeds the {limit}-byte limit",
                )
            content.extend(chunk[: max(0, remaining)])
            return bytes(content), True
        content.extend(chunk)
    return bytes(content), False


def build_target_url(
    base_url: str | None,
    path: str,
    query_items: Sequence[QueryItem] = (),
) -> httpx.URL:
    if not base_url:
        raise TargetHTTPTransportError(
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
        raise TargetHTTPTransportError("invalid_base_url", "The App target base URL is invalid")
    validate_relative_target_path(path)
    base_path = parsed.path.rstrip("/")
    url = httpx.URL(urlunsplit((parsed.scheme, parsed.netloc, f"{base_path}{path}", "", "")))
    return url.copy_with(query=_encode_query(query_items)) if query_items else url


def _encode_query(query_items: Sequence[QueryItem]) -> bytes:
    parts: list[str] = []
    # `#` cannot appear literally in an HTTP query component because it starts
    # a URI fragment. All other RFC 3986 reserved characters are preserved when
    # the OpenAPI parameter explicitly opts into allowReserved.
    reserved = ":/?[]@!$&'()*+,;="
    for item in query_items:
        if len(item) == 2:
            name, value = item
            allow_reserved = False
        else:
            name, value, allow_reserved = item
        encoded_name = quote(name, safe="")
        encoded_value = quote(value, safe=reserved if allow_reserved else "")
        parts.append(f"{encoded_name}={encoded_value}")
    return "&".join(parts).encode("ascii")


def validate_relative_target_path(path: str) -> None:
    if not path.startswith("/") or path.startswith("//") or "?" in path or "#" in path or "\\" in path:
        raise TargetHTTPTransportError(
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
        raise TargetHTTPTransportError("invalid_path", "HTTP request path cannot contain dot segments")


def merge_target_headers(
    context_headers: Mapping[str, str],
    request_headers: Mapping[str, str],
    *,
    override_context_headers: bool,
    allowed_sensitive_request_headers: Collection[str] = (),
) -> dict[str, str]:
    merged = dict(context_headers)
    existing = {name.lower(): name for name in merged}
    allowed_sensitive = {
        name.strip().lower() for name in allowed_sensitive_request_headers
    }
    for name, value in request_headers.items():
        normalized = name.strip().lower()
        sensitive = is_sensitive_header(normalized)
        if (sensitive and normalized not in allowed_sensitive) or normalized in HOP_BY_HOP_HEADERS:
            raise TargetHTTPTransportError(
                "forbidden_header",
                f"HTTP request header cannot be set per call: {name}",
            )
        previous = existing.get(normalized)
        if sensitive and previous is not None:
            if normalized == "cookie":
                merged[previous] = _merge_cookie_values(merged[previous], value)
            continue
        if previous is not None and not override_context_headers:
            continue
        if previous is not None:
            merged.pop(previous)
        merged[name] = value
        existing[normalized] = name
    return merged


def is_sensitive_header(name: str) -> bool:
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


def _merge_cookie_values(context_value: str, generated_value: str) -> str:
    """Append generated cookies without replacing same-name context cookies."""

    context_parts = [part.strip() for part in context_value.split(";") if part.strip()]
    context_names = {
        part.split("=", 1)[0].strip().lower()
        for part in context_parts
    }
    generated_parts = [
        part.strip()
        for part in generated_value.split(";")
        if part.strip()
        and part.split("=", 1)[0].strip().lower() not in context_names
    ]
    return "; ".join([*context_parts, *generated_parts])
