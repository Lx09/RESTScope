"""Implement request-input discovery and exact Schema lookup.

Both behaviors resolve one Operation through a trusted callback and return
bounded semantic handles or one selected Schema summary. They never inspect
retained response observations.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from restscope.openapi_parser.ir import OperationIR
from restscope.operation_references import RequestInputReference
from restscope.tools.runtime import ToolFailure

from .projection import _input_schema_summary
from .traversal import (
    _DEFAULT_LIST_LIMIT,
    _operation_inputs,
    _ordinary_input_entries,
    _schema_entries,
    _select_media_schema,
)


def list_inputs(
    *,
    operation_resolver: Callable[[str], OperationIR],
    operation_key: str,
    media_type: str | None = None,
    prefix: str | None = None,
    offset: int = 0,
    limit: int = _DEFAULT_LIST_LIMIT,
) -> dict[str, Any]:
    """Return one deterministic page of request handles.

    ``media_type`` filters only request-body entries because ordinary HTTP
    Parameters do not vary by request content type.  ``prefix`` uses exact
    handle text, so callers can narrow a large body without retrieving its
    Schemas.  The toolbox validates numeric limits before this method runs.
    """
    operation = operation_resolver(operation_key)
    entries = [
        entry
        for entry in _operation_inputs(operation)
        if (
            (
                entry.media_type is None
                or media_type is None
                or entry.media_type == media_type
            )
            and (prefix is None or entry.name.startswith(prefix))
        )
    ]
    entries.sort(key=lambda item: (item.name, item.media_type or ""))
    page = entries[offset : offset + limit]
    inputs = [
        {
            "name": entry.name,
            **(
                {"media_type": entry.media_type}
                if entry.media_type is not None
                else {}
            ),
        }
        for entry in page
    ]
    result: dict[str, Any] = {
        "operation_key": operation.operation_key,
        "inputs": inputs,
        "total": len(entries),
        "offset": offset,
    }
    next_offset = offset + len(page)
    if next_offset < len(entries):
        result["next_offset"] = next_offset
    return {"structured": result}


def get_input_schema(
    *,
    operation_resolver: Callable[[str], OperationIR],
    operation_key: str,
    input: str,
    media_type: str | None = None,
) -> dict[str, Any]:
    """Return the compact Schema for one exact request-input handle.

    Body handles are resolved inside one selected request media type.
    Supplying a media type for path, query, header, or cookie input is an
    actionable caller mistake rather than a silently ignored argument.
    """
    operation = operation_resolver(operation_key)
    if input == "body" or input.startswith("body.") or input.startswith("body["):
        selected_media_type, schema = _select_media_schema(
            operation.request_body.contents if operation.request_body else {},
            requested=media_type,
            subject="request body",
        )
        entries = _schema_entries(
            schema,
            name="body",
            required=bool(operation.request_body and operation.request_body.required),
            location="body",
            media_type=selected_media_type,
            skip_read_only=True,
            reference=RequestInputReference.body(),
        )
    else:
        if media_type is not None:
            raise ToolFailure(
                code="openapi_input_media_type_not_allowed",
                message="media_type is accepted only for body inputs.",
            )
        selected_media_type = None
        entries = _ordinary_input_entries(operation)

    entry = next((item for item in entries if item.name == input), None)
    if entry is None:
        raise ToolFailure(
            code="openapi_input_not_found",
            message=(
                f"OpenAPI input was not found in {operation.operation_key}: {input}"
            ),
        )
    result = {
        "operation_key": operation.operation_key,
        "input": entry.name,
        "location": entry.location,
        "required": entry.required,
        **(
            {"media_type": selected_media_type}
            if selected_media_type is not None
            else {}
        ),
        "schema": _input_schema_summary(entry.schema),
    }
    return {"structured": result}
