"""Shared target-bound HTTP transport primitives."""

from __future__ import annotations

from collections.abc import Callable, Collection, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
import re
from typing import Any
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
    url: httpx.URL
    headers: dict[str, str]


class TargetHTTPTransportError(RuntimeError):
    """Stable target transport error without remote exception details."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class TargetHTTPTimeout(TimeoutError):
    """One target request exceeded its configured timeout."""


class TargetHTTPTransport:
    """Open isolated response streams against one App-bound target."""

    def __init__(self, *, client_factory: ClientFactory = httpx.Client) -> None:
        self.client_factory = client_factory

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
