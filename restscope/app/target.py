"""Validate and build the single target bound to one RESTScope App.

The module accepts a file, URL, or inline OpenAPI source plus the target base
URL and shared headers. It returns an immutable :class:`ToolContext` only after
the document contains testable operations and all target HTTP inputs are safe.
The App runtime owns when this work may happen; the Harness owns the resulting
Context after binding.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from restscope.openapi_parser import OpenAPIParser
from restscope.target_api.request import (
    validate_target_base_url,
    validate_target_headers,
)
from restscope.tools.context import ToolContext


class _SchemaSourceModel(BaseModel):
    """Reject unknown fields in one App initialization source."""

    model_config = ConfigDict(extra="forbid")


class _FileSchemaSource(_SchemaSourceModel):
    """Read an OpenAPI document from one local filesystem path."""

    kind: Literal["file"]
    path: str


class _UrlSchemaSource(_SchemaSourceModel):
    """Read an OpenAPI document from one explicit URL."""

    kind: Literal["url"]
    url: str


class _InlineSchemaSource(_SchemaSourceModel):
    """Parse an OpenAPI document supplied directly by the App caller."""

    kind: Literal["inline"]
    format: Literal["yaml", "json"] = "yaml"
    content: str


_SchemaSource = Annotated[
    _FileSchemaSource | _UrlSchemaSource | _InlineSchemaSource,
    Field(discriminator="kind"),
]


def _build_target_context(
    *,
    schema_source: Mapping[str, object],
    base_url: str,
    headers: Mapping[str, str] | None,
) -> ToolContext:
    """Validate one target and return the Context published to the Harness.

    Args:
        schema_source: Closed file, URL, or inline OpenAPI source object.
        base_url: Required HTTP or HTTPS target base URL.
        headers: Optional App-lifetime target headers, including credentials.

    Returns:
        A detached target Context containing parsed operations and copied input.

    Raises:
        TargetAPIError: The target base URL is missing or unsafe.
        ValueError: Headers or the OpenAPI document are invalid, or the document
            contains no testable operation.
    """
    validate_target_base_url(base_url)
    validate_target_headers(headers or {})
    source = TypeAdapter(_SchemaSource).validate_python(dict(schema_source))
    ir = OpenAPIParser.parse(_schema_source_value(source))
    parser_errors = [
        *ir.diagnostics.spec_errors,
        *ir.diagnostics.path_errors,
        *ir.diagnostics.operation_errors,
    ]
    if parser_errors:
        first = parser_errors[0]
        raise ValueError(
            f"OpenAPI parsing produced {len(parser_errors)} error(s): "
            f"{first.message}"
        )
    if not ir.operations:
        raise ValueError("OpenAPI schema contains no testable operations")
    return ToolContext(
        ir=ir,
        baseline_schema_source=source.model_dump(mode="json"),
        base_url=base_url,
        headers=headers or {},
    )


def _schema_source_value(source: _SchemaSource) -> str:
    """Return the parser input represented by one validated source variant.

    File and URL variants pass their location to the parser; inline variants
    pass the document body. The discriminated source model guarantees that the
    matching field exists before this branch runs.
    """
    if source.kind == "file":
        return source.path
    if source.kind == "url":
        return source.url
    return source.content
