"""Implement response-field discovery and exact Schema lookup.

These behaviors select one declared response and media type, traverse its body
Schema, and return stable field handles or one bounded Schema summary.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from restscope.openapi_parser.ir import OperationIR
from restscope.operation_references import ResponseFieldReference
from restscope.tools.runtime import ToolFailure

from .projection import _schema_summary
from .traversal import (
    _DEFAULT_LIST_LIMIT,
    _normalize_response_field,
    _normalize_status_code,
    _schema_entries,
    _select_media_schema,
    _select_response,
)


def list_response_fields(
    *,
    operation_resolver: Callable[[str], OperationIR],
    operation_key: str,
    status_code: int | str,
    offset: int = 0,
    limit: int = _DEFAULT_LIST_LIMIT,
) -> dict[str, Any]:
    """Return one deterministic page of response-body field handles.

    The operation and response status select one response contract. The
    selected response is expected to have one Schema-bearing media type;
    existing lookup failures report missing or ambiguous contracts. The
    toolbox validates pagination values before this method runs.
    """
    operation = operation_resolver(operation_key)
    requested_status = _normalize_status_code(status_code)
    matched_status, response = _select_response(
        operation,
        requested_status=requested_status,
    )
    selected_media_type, schema = _select_media_schema(
        response.contents,
        requested=None,
        subject=f"response {matched_status}",
    )
    entries = _schema_entries(
        schema,
        name="body",
        required=False,
        location="body",
        media_type=selected_media_type,
        skip_write_only=True,
        reference=ResponseFieldReference.body(),
    )
    entries.sort(key=lambda item: item.name)
    page = entries[offset : offset + limit]
    result: dict[str, Any] = {
        "operation_key": operation.operation_key,
        "requested_status_code": requested_status,
        "matched_status_code": matched_status,
        "media_type": selected_media_type,
        "fields": [{"name": entry.name} for entry in page],
        "total": len(entries),
        "offset": offset,
    }
    next_offset = offset + len(page)
    if next_offset < len(entries):
        result["next_offset"] = next_offset
    return {"structured": result}

def get_response_field_schema(
    *,
    operation_resolver: Callable[[str], OperationIR],
    operation_key: str,
    status_code: int | str,
    field: str,
    media_type: str | None = None,
) -> dict[str, Any]:
    """Return the compact Schema for one exact response-body field.

    Numeric array indexes from concrete Catalog evidence are normalized to
    the ``[]`` Schema handle.  OpenAPI combiner branch indexes such as
    ``anyOf[0]`` remain intact because they select a Schema branch rather
    than one runtime array element.
    """
    operation = operation_resolver(operation_key)
    requested_status = _normalize_status_code(status_code)
    matched_status, response = _select_response(
        operation,
        requested_status=requested_status,
    )
    selected_media_type, schema = _select_media_schema(
        response.contents,
        requested=media_type,
        subject=f"response {matched_status}",
    )
    normalized_field = _normalize_response_field(field)
    entries = _schema_entries(
        schema,
        name="body",
        required=False,
        location="body",
        media_type=selected_media_type,
        skip_write_only=True,
        reference=ResponseFieldReference.body(),
    )
    entry = next(
        (item for item in entries if item.name == normalized_field),
        None,
    )
    if entry is None:
        raise ToolFailure(
            code="openapi_response_field_not_found",
            message=(
                "OpenAPI response field was not found in "
                f"{operation.operation_key} {matched_status} "
                f"{selected_media_type}: {normalized_field}"
            ),
        )
    return {
        "structured": {
            "operation_key": operation.operation_key,
            "requested_status_code": requested_status,
            "matched_status_code": matched_status,
            "field": entry.name,
            "media_type": selected_media_type,
            "required": entry.required,
            "schema": _schema_summary(entry.schema),
        }
    }
