"""Validate and prepare requests that may be sent to the configured target API.

The module accepts the App-owned target origin plus operation-relative request
parts and returns a :class:`PreparedTargetRequest`. It is the URL and header
trust seam used before :mod:`restscope.target_api.client` opens a network
connection.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from urllib.parse import SplitResult, quote, unquote, urlsplit, urlunsplit

import httpx

from .errors import TargetAPIError

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
_HEADER_NAME_PATTERN = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")


@dataclass(slots=True, frozen=True)
class PreparedTargetRequest:
    """Hold a target-validated request before any HTTP client is opened.

    ``url`` contains the configured target origin and the validated relative
    path. ``headers`` is a fresh mapping whose generated values cannot replace
    App-owned secrets unless the caller explicitly permits the Cookie case.
    """

    method: str
    path: str
    url: httpx.URL
    headers: dict[str, str]


def prepare_target_request(
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
    """Validate and resolve one target request without opening a connection.

    Callers may prepare a complete Batch before the first target side effect.
    Invalid origins, paths, or request-header overrides raise
    :class:`TargetAPIError` before any HTTP client exists.
    """

    validate_target_headers(context_headers or {})
    validate_target_headers(request_headers or {})
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


def build_target_url(
    base_url: str | None,
    path: str,
    query_items: Sequence[QueryItem] = (),
) -> httpx.URL:
    """Resolve a validated relative path against the configured target origin.

    Raises:
        TargetAPIError: If the origin contains credentials,
            hidden query state, or a fragment, or if ``path`` could escape the
            configured target origin.
    """

    parsed = validate_target_base_url(base_url)
    validate_relative_target_path(path)
    base_path = parsed.path.rstrip("/")
    url = httpx.URL(
        urlunsplit((parsed.scheme, parsed.netloc, f"{base_path}{path}", "", ""))
    )
    return url.copy_with(query=_encode_query(query_items)) if query_items else url


def validate_target_base_url(base_url: str | None) -> SplitResult:
    """Return a safe parsed target URL shared by App and request validation.

    Args:
        base_url: Candidate HTTP or HTTPS origin, optionally with a path prefix.

    Returns:
        Parsed URL parts suitable for same-origin request construction.

    Raises:
        TargetAPIError: The URL is missing, is not HTTP(S), lacks a host, or
            carries credentials, hidden query state, or a fragment.
    """
    if not base_url:
        raise TargetAPIError(
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
        raise TargetAPIError(
            "invalid_base_url",
            "The App target base URL is invalid",
        )
    return parsed


def validate_target_origin(base_url: str | None) -> SplitResult:
    """Return a safe origin for a standalone RESTScope target.

    Args:
        base_url: Candidate HTTP or HTTPS origin supplied at process startup.

    Returns:
        Parsed URL parts with no path beyond an optional trailing slash.

    Raises:
        TargetAPIError: General base URL validation fails or the URL contains a
            path prefix. Request preparation may still use a validated base
            path; the standalone command deliberately binds an origin.
    """
    parsed = validate_target_base_url(base_url)
    if parsed.path not in {"", "/"}:
        raise TargetAPIError(
            "invalid_base_url",
            "The App target base URL must be an HTTP or HTTPS origin",
        )
    return parsed


def validate_relative_target_path(path: str) -> None:
    """Reject cross-origin, query-bearing, or traversal-like target paths."""

    if (
        not path.startswith("/")
        or path.startswith("//")
        or "?" in path
        or "#" in path
        or "\\" in path
    ):
        raise TargetAPIError(
            "invalid_path",
            "HTTP request path must be a single-slash relative target path",
        )
    # Decode repeatedly so a nested percent encoding cannot hide a dot segment.
    # The original path remains untouched and is sent only after this check.
    decoded = path
    for _ in range(3):
        expanded = unquote(decoded)
        if expanded == decoded:
            break
        decoded = expanded
    if any(segment in {".", ".."} for segment in decoded.split("/")):
        raise TargetAPIError(
            "invalid_path",
            "HTTP request path cannot contain dot segments",
        )


def merge_target_headers(
    context_headers: Mapping[str, str],
    request_headers: Mapping[str, str],
    *,
    override_context_headers: bool,
    allowed_sensitive_request_headers: Collection[str] = (),
) -> dict[str, str]:
    """Merge App headers with restricted per-request generated headers.

    Authentication normally comes only from ``context_headers``. Generated
    requests cannot replace secrets or connection-management headers. Cookie
    is the narrow exception when the caller explicitly grants it; even then,
    generated cookies cannot replace an App-owned cookie with the same name.

    Raises:
        TargetAPIError: If a generated header crosses the
            connection-management or secret seam.
    """

    merged = dict(context_headers)
    existing = {name.lower(): name for name in merged}
    allowed_sensitive = {
        name.strip().lower() for name in allowed_sensitive_request_headers
    }
    for name, value in request_headers.items():
        normalized = name.strip().lower()
        sensitive = is_sensitive_header(normalized)
        if (
            sensitive and normalized not in allowed_sensitive
        ) or normalized in HOP_BY_HOP_HEADERS:
            raise TargetAPIError(
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


def validate_target_headers(headers: Mapping[str, str]) -> None:
    """Reject malformed names and values before target request persistence.

    Args:
        headers: App-level or per-request HTTP headers to inspect.

    Raises:
        ValueError: A name is not an HTTP token, or a name/value contains a NUL
            or line break that could create another header field.
    """
    seen: set[str] = set()
    for name, value in headers.items():
        normalized = name.lower()
        if not _HEADER_NAME_PATTERN.fullmatch(name):
            raise ValueError("Target header names must be valid HTTP tokens")
        if normalized in seen:
            raise ValueError("Target header names must be unique ignoring case")
        if any(
            character in name or character in value
            for character in ("\r", "\n", "\0")
        ):
            raise ValueError("Target headers cannot contain line breaks or NUL bytes")
        seen.add(normalized)


def is_sensitive_header(name: str) -> bool:
    """Return whether a header name conventionally carries a secret."""

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


def _encode_query(query_items: Sequence[QueryItem]) -> bytes:
    """Encode ordered OpenAPI query values while honoring ``allowReserved``."""

    parts: list[str] = []
    # ``#`` can never remain literal because it begins a URI fragment. Other
    # reserved characters are preserved only for an explicit allowReserved item.
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


def _merge_cookie_values(context_value: str, generated_value: str) -> str:
    """Append generated cookies without replacing same-name App cookies."""

    context_parts = [part.strip() for part in context_value.split(";") if part.strip()]
    context_names = {
        part.split("=", 1)[0].strip().lower() for part in context_parts
    }
    generated_parts = [
        part.strip()
        for part in generated_value.split(";")
        if part.strip()
        and part.split("=", 1)[0].strip().lower() not in context_names
    ]
    return "; ".join([*context_parts, *generated_parts])
