"""Parse one executed request outcome into bounded inline Failure evidence.

Generic Batch execution calls these helpers once at the HTTP capture seam. The
normalized messages enter only that Tool result; there is no Test Case Catalog,
Failure Memory, or persistence boundary behind them.
"""

from __future__ import annotations

from restscope.target_api.media_type import is_json_media_type, normalize_media_type

from .outcomes import HTTPFailure, TransportFailure


_JSON_KEYS = ("message", "detail", "error", "title", "errors")
_FIELD_KEYS = ("field", "path", "name")
_MAX_MESSAGE_CHARS = 4_096
_MAX_MESSAGES = 100


def parse_http_failure(
    *,
    status_code: int,
    reason_phrase: str,
    media_type: str | None,
    response_body: object | None,
    body_truncated: bool,
) -> HTTPFailure | None:
    """Return parsed evidence for a non-2xx response, or ``None`` for success."""
    if 200 <= status_code < 300:
        return None
    fallback = _bound(
        f"HTTP {status_code}"
        + (
            f" {_normalize_text(reason_phrase)}"
            if reason_phrase
            else ""
        )
    )
    normalized_media = normalize_media_type(media_type)
    messages: list[str] = []
    if 400 <= status_code < 600 and body_truncated:
        messages = [fallback + " [failure body truncated]"]
    elif 400 <= status_code < 600 and is_json_media_type(normalized_media):
        extracted = _extract_json_messages(response_body)[:_MAX_MESSAGES]
        messages = [
            _bound(f"HTTP {status_code}: {item}")
            for item in extracted
        ]
    elif (
        400 <= status_code < 600
        and normalized_media is not None
        and normalized_media.startswith("text/")
        and isinstance(response_body, str)
    ):
        detail = _normalize_text(response_body)
        messages = [_bound(f"HTTP {status_code}: {detail}")] if detail else []
    return HTTPFailure(
        status_code=status_code,
        messages=messages or [fallback],
        body_truncated=body_truncated,
    )


def parse_transport_failure(*, code: str, message: str) -> TransportFailure:
    """Normalize a request attempt that produced no HTTP response."""
    normalized_code = _normalize_text(code or "transport_error")
    normalized_message = _normalize_text(message)
    rendered = (
        f"TRANSPORT {normalized_code}: {normalized_message}"
        if normalized_message
        else f"TRANSPORT {normalized_code}"
    )
    return TransportFailure(
        code=normalized_code,
        messages=[_bound(rendered)],
    )


def _extract_json_messages(value: object, *, depth: int = 0) -> list[str]:
    """Find conventional API error fields without traversing arbitrary bodies."""
    if depth > 8 or not isinstance(value, dict):
        return []
    for key in _JSON_KEYS:
        if key not in value:
            continue
        result = _extract_json_field(value[key], depth=depth + 1)
        if result:
            return result
    return []


def _extract_json_field(value: object, *, depth: int) -> list[str]:
    """Extract strings and field-aware entries from one conventional error key."""
    if isinstance(value, str):
        normalized = _normalize_text(value)
        return [normalized] if normalized else []
    if isinstance(value, list):
        return [
            message
            for item in value
            for message in _extract_error_item(item, depth=depth + 1)
        ]
    if isinstance(value, dict):
        nested = _extract_json_messages(value, depth=depth + 1)
        if nested:
            return nested
        return [
            message
            for field, child in value.items()
            for message in _extract_field_messages(
                field=_normalize_text(str(field)),
                value=child,
                depth=depth + 1,
            )
        ]
    return []


def _extract_error_item(value: object, *, depth: int) -> list[str]:
    """Retain a field name when an API returns structured error arrays."""
    if isinstance(value, str):
        normalized = _normalize_text(value)
        return [normalized] if normalized else []
    if not isinstance(value, dict) or depth > 8:
        return []
    messages = _extract_json_messages(value, depth=depth + 1)
    if not messages:
        messages = [
            message
            for key, child in value.items()
            if key not in _FIELD_KEYS
            for message in _extract_field_messages(
                field=_normalize_text(str(key)),
                value=child,
                depth=depth + 1,
            )
        ]
    field = next(
        (
            _normalize_text(str(value[key]))
            for key in _FIELD_KEYS
            if isinstance(value.get(key), (str, int))
        ),
        "",
    )
    return [f"{field}: {message}" for message in messages] if field else messages


def _extract_field_messages(
    *,
    field: str,
    value: object,
    depth: int,
) -> list[str]:
    """Flatten one field-keyed validation subtree into bounded semantic text."""
    if depth > 8 or not field:
        return []
    if isinstance(value, str):
        message = _normalize_text(value)
        return [f"{field}: {message}"] if message else []
    if isinstance(value, list):
        output: list[str] = []
        for item in value:
            if isinstance(item, str):
                message = _normalize_text(item)
                if message:
                    output.append(f"{field}: {message}")
            else:
                output.extend(
                    f"{field}: {message}"
                    for message in _extract_error_item(
                        item,
                        depth=depth + 1,
                    )
                )
        return output
    if isinstance(value, dict):
        return [
            message
            for child_field, child in value.items()
            for message in _extract_field_messages(
                field=f"{field}.{_normalize_text(str(child_field))}",
                value=child,
                depth=depth + 1,
            )
        ]
    return []


def _normalize_text(value: str) -> str:
    """Collapse whitespace so formatting-only differences share a Fingerprint."""
    return " ".join(value.split())


def _bound(value: str) -> str:
    """Keep one Failure Message within the reviewed Agent allowance."""
    if len(value) <= _MAX_MESSAGE_CHARS:
        return value
    remaining = _MAX_MESSAGE_CHARS - 80
    head = remaining // 2
    tail = remaining - head
    return (
        f"{value[:head]}…[message clipped; chars={len(value)}]…{value[-tail:]}"
    )
