"""Project trace and bounded HTTP values into the live UI's stable vocabulary.

These helpers are pure transformations: they classify Tool names, derive card
status and summaries, propagate semantic scope, and encode already-bounded
request or response bodies. They neither own observer state nor publish cursor
changes.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Literal

from restscope.target_http.request import is_json_media_type, normalize_media_type


EventKind = Literal["agent_turn", "tool_call"]
HTTP_TOOL_NAME = "restscope.http.request"


def classify_tool(name: str) -> str:
    """Map a concrete Tool name to one stable visual family."""

    if name.startswith("plan."):
        return "plan"
    if name.startswith("openapi."):
        return "openapi"
    if name.startswith("test_case."):
        return "test_case"
    if name.startswith("request_generation.") or name.startswith("parameter_patch."):
        return "parameter_patch"
    if name.startswith("resource."):
        return "resource"
    if name == HTTP_TOOL_NAME:
        return "http"
    if name.startswith("mcp."):
        return "mcp"
    return "other"


def event_summary(
    *,
    kind: EventKind,
    name: str,
    attributes: Mapping[str, object],
) -> str:
    """Build the short redundant label shown while a card is collapsed."""

    if kind == "agent_turn":
        return f"Agent turn · {name}"
    if kind == "tool_call":
        return f"{classify_tool(name).replace('_', ' ').title()} · {name}"
    raise ValueError(f"Unsupported live event kind: {kind}")


def merge_scope(
    parent: Mapping[str, object],
    attributes: Mapping[str, object],
) -> dict[str, object]:
    """Propagate only identifiers needed to own semantic cards and Batch rows."""

    scope = dict(parent)
    mappings = {
        "restscope.operation.key": "operation_key",
        "restscope.operation.round": "round_number",
        "restscope.test.run_id": "test_run_id",
        "restscope.test.case_id": "case_id",
        "restscope.test.case_index": "case_index",
    }
    for source, destination in mappings.items():
        if attributes.get(source) is not None:
            scope[destination] = attributes[source]
    return scope


def semantic_status(
    *,
    event: Mapping[str, object],
    output: object,
    direction: str,
) -> str | None:
    """Derive visible Tool and Batch status from their completed output."""

    if direction != "output" or not isinstance(output, dict):
        return None
    if event.get("kind") == "tool_call":
        status = output.get("status")
        return tool_status(str(status)) if status is not None else None
    return None


def tool_status(status: str) -> str:
    """Map ToolResult status to the four visual event states."""

    if status in {"failed", "timed_out"}:
        return "failed"
    if status in {"denied", "warning"}:
        return "warning"
    return "succeeded"


def message_fingerprint(message: Mapping[str, object]) -> str:
    """Produce a stable comparison key for repeated full prompt snapshots."""

    return json.dumps(message, ensure_ascii=False, sort_keys=True, default=str)


def request_body(request_kwargs: Mapping[str, object]) -> dict[str, object] | None:
    """Project the transport's one selected body encoding for safe display."""

    for key in ("json", "content", "data"):
        if key not in request_kwargs:
            continue
        value = request_kwargs[key]
        if isinstance(value, bytes | bytearray):
            return decode_body(bytes(value), media_type=None, encoding="utf-8")
        return {"format": "json" if key == "json" else "text", "value": value}
    return None


def response_detail(response: object) -> dict[str, object]:
    """Convert one already-bounded response into JSON, text, or Base64 evidence."""

    headers = dict(getattr(response, "headers", {}) or {})
    media_type = normalize_media_type(headers.get("content-type")) or ""
    body = getattr(response, "body", None)
    retained_size = len(body) if isinstance(body, bytes | bytearray) else None
    size_bytes = reported_response_size(headers, retained_size)
    return {
        "status_code": getattr(response, "status_code", None),
        "reason_phrase": getattr(response, "reason_phrase", ""),
        "url": getattr(response, "url", ""),
        "headers": headers,
        "body": (
            decode_body(
                bytes(body),
                media_type=media_type or None,
                encoding=getattr(response, "encoding", None) or "utf-8",
            )
            if isinstance(body, bytes | bytearray)
            else None
        ),
        "body_retained": body is not None,
        "body_truncated": bool(getattr(response, "body_truncated", False)),
        "size_bytes": size_bytes,
        "retained_size_bytes": retained_size,
        "processor_result": getattr(response, "processor_result", None),
    }


def reported_response_size(
    headers: Mapping[str, object],
    retained_size: int | None,
) -> int | None:
    """Prefer a valid Content-Length while retaining the stored byte count."""

    raw = next(
        (
            value
            for name, value in headers.items()
            if str(name).casefold() == "content-length"
        ),
        None,
    )
    try:
        return int(raw) if raw is not None else retained_size
    except (TypeError, ValueError):
        return retained_size


def decode_body(
    content: bytes,
    *,
    media_type: str | None,
    encoding: str,
) -> dict[str, object]:
    """Decode bounded bytes without pretending binary evidence is text."""

    if is_json_media_type(media_type):
        try:
            return {"format": "json", "value": json.loads(content.decode(encoding))}
        except (LookupError, UnicodeDecodeError, json.JSONDecodeError):
            pass
    textual = (
        media_type is None
        or media_type.startswith("text/")
        or media_type.endswith("+xml")
        or media_type
        in {
            "application/graphql",
            "application/javascript",
            "application/x-www-form-urlencoded",
            "application/x-yaml",
            "application/xml",
            "application/yaml",
        }
    )
    if textual:
        try:
            return {"format": "text", "value": content.decode(encoding)}
        except (LookupError, UnicodeDecodeError):
            pass
    return {"format": "base64", "value": base64.b64encode(content).decode("ascii")}


def utc_now() -> str:
    """Return one timezone-explicit timestamp suitable for lexical display."""

    return datetime.now(UTC).isoformat()
