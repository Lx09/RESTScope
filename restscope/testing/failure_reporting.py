"""Build bounded, deterministic failure messages from batch response evidence."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
import json
from typing import Any, Iterable

from .models import BatchFailureReport, UniqueFailureMessage


MAX_FAILURE_RESPONSE_BYTES = 1024 * 1024
MAX_FAILURE_MESSAGE_BYTES = 4 * 1024
MAX_UNIQUE_FAILURE_MESSAGES = 100
_TRUNCATED_MESSAGE_SUFFIX = "…[failure message truncated]"
_JSON_KEYS = ("message", "detail", "error", "title", "errors")
_FIELD_KEYS = ("field", "path", "name")


@dataclass(frozen=True, slots=True)
class FailureCaseEvidence:
    """Private response projection needed to report one failed case."""

    case_id: str
    status_code: int | None = None
    reason_phrase: str | None = None
    media_type: str | None = None
    body: bytes | None = None
    body_truncated: bool = False
    encoding: str | None = None
    transport_error_code: str | None = None
    transport_error_message: str | None = None


def build_batch_failure_report(
    cases: Iterable[FailureCaseEvidence],
) -> BatchFailureReport:
    """Deduplicate normalized messages while retaining first-seen order."""

    messages: OrderedDict[str, list[str]] = OrderedDict()
    truncated = False
    for case in cases:
        for message in _case_messages(case):
            case_ids = messages.get(message)
            if case_ids is not None:
                if case.case_id not in case_ids:
                    case_ids.append(case.case_id)
                continue
            if len(messages) >= MAX_UNIQUE_FAILURE_MESSAGES:
                truncated = True
                continue
            messages[message] = [case.case_id]
    return BatchFailureReport(
        unique_failure_messages=[
            UniqueFailureMessage(
                failure_id=f"f{index}",
                message=message,
                case_ids=case_ids,
            )
            for index, (message, case_ids) in enumerate(messages.items(), start=1)
        ],
        truncated=truncated,
    )


def _case_messages(case: FailureCaseEvidence) -> list[str]:
    """
    Handle case messages as part of deterministic request generation, constraint
    solving, and execution.

    This private helper keeps one transformation or policy decision explicit so the
    surrounding orchestration remains readable.
    """
    if case.transport_error_code is not None:
        detail = _normalize_text(case.transport_error_message or "")
        base = f"TRANSPORT {case.transport_error_code}"
        return [_bound_message(f"{base}: {detail}" if detail else base)]
    status = case.status_code
    if status is None or 200 <= status < 300:
        return []
    fallback = _http_fallback(
        status,
        case.reason_phrase,
        body_truncated=case.body_truncated and _is_json(case.media_type),
    )
    if not case.body:
        return [fallback]
    if _looks_binary(case.body, case.encoding):
        return [fallback]
    if _is_json(case.media_type):
        if case.body_truncated:
            return [fallback]
        try:
            payload = json.loads(_decode(case.body, case.encoding))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return [fallback]
        extracted = _extract_json_messages(payload)
        return [
            _bound_message(f"HTTP {status}: {message}")
            for message in extracted
        ] or [fallback]
    if _is_text(case.media_type):
        detail = _normalize_text(_decode(case.body, case.encoding, errors="replace"))
        return [_bound_message(f"HTTP {status}: {detail}")] if detail else [fallback]
    return [fallback]


def _extract_json_messages(value: Any, *, depth: int = 0) -> list[str]:
    if depth > 8 or not isinstance(value, dict):
        return []
    for key in _JSON_KEYS:
        if key not in value:
            continue
        extracted = _extract_json_field(key, value[key], depth=depth + 1)
        if extracted:
            return extracted
    return []


def _extract_json_field(key: str, value: Any, *, depth: int) -> list[str]:
    if isinstance(value, str):
        normalized = _normalize_text(value)
        return [normalized] if normalized else []
    if key == "errors" and isinstance(value, list):
        output: list[str] = []
        for item in value:
            output.extend(_extract_error_item(item, depth=depth + 1))
        return output
    if isinstance(value, dict):
        return _extract_json_messages(value, depth=depth + 1)
    return []


def _extract_error_item(value: Any, *, depth: int) -> list[str]:
    if isinstance(value, str):
        normalized = _normalize_text(value)
        return [normalized] if normalized else []
    if not isinstance(value, dict) or depth > 8:
        return []
    extracted = _extract_json_messages(value, depth=depth + 1)
    if not extracted:
        return []
    field = next(
        (
            _normalize_text(str(value[key]))
            for key in _FIELD_KEYS
            if key in value and isinstance(value[key], (str, int))
        ),
        "",
    )
    if not field:
        return extracted
    return [f"{field}: {message}" for message in extracted]


def _http_fallback(
    status: int,
    reason_phrase: str | None,
    *,
    body_truncated: bool,
) -> str:
    reason = _normalize_text(reason_phrase or "")
    message = f"HTTP {status}" + (f" {reason}" if reason else "")
    if body_truncated:
        message += " [failure body truncated]"
    return _bound_message(message)


def _is_json(media_type: str | None) -> bool:
    normalized = _normalized_media_type(media_type)
    return normalized == "application/json" or normalized.endswith("+json")


def _is_text(media_type: str | None) -> bool:
    return _normalized_media_type(media_type).startswith("text/")


def _normalized_media_type(media_type: str | None) -> str:
    return (media_type or "").split(";", 1)[0].strip().lower()


def _decode(
    value: bytes,
    encoding: str | None,
    *,
    errors: str = "strict",
) -> str:
    return value.decode(encoding or "utf-8", errors=errors)


def _looks_binary(value: bytes, encoding: str | None) -> bool:
    sample = value[:4096]
    try:
        decoded = _decode(sample, encoding)
    except (LookupError, UnicodeDecodeError):
        return True
    return any(
        ord(character) < 32 and character not in "\t\n\r"
        for character in decoded
    )


def _normalize_text(value: str) -> str:
    return " ".join(value.split())


def _bound_message(message: str) -> str:
    encoded = message.encode("utf-8")
    if len(encoded) <= MAX_FAILURE_MESSAGE_BYTES:
        return message
    suffix = _TRUNCATED_MESSAGE_SUFFIX.encode("utf-8")
    available = MAX_FAILURE_MESSAGE_BYTES - len(suffix)
    prefix = encoded[:available].decode("utf-8", errors="ignore")
    return f"{prefix}{_TRUNCATED_MESSAGE_SUFFIX}"
