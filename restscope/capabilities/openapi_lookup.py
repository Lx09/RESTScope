"""Register the global read-only OpenAPI operation lookup capability.

The handler reads the App-bound OpenAPI IR and returns one compact JSON
projection. Dedup uses it to discover semantic request handles without placing
the Parameter catalog in its initial prompt. The projection intentionally omits
descriptions, examples, raw schemas, and security configuration.
"""

from __future__ import annotations

from typing import Any

from restscope.llm import ToolSpec
from restscope.openapi_parser.ir import OperationIR, SchemaIR

from .tool_context import ToolContext
from .tool_registry import ToolRegistry


OPENAPI_LOOKUP_TOOL_NAME = "openapi.lookup_operation"


class OpenAPILookupError(LookupError):
    """Expose a stable capability error without leaking other operation data."""

    code = "openapi_operation_not_found"


def register_openapi_lookup_tool(registry: ToolRegistry) -> ToolSpec:
    """Register operation-key lookup in the shared capability runtime."""
    spec = ToolSpec(
        name=OPENAPI_LOOKUP_TOOL_NAME,
        description=(
            "List the request Parameters and request-body fields declared by "
            "one OpenAPI operation. Use the exact RESTScope operation_key."
        ),
        kind="local_function",
        input_schema={
            "type": "object",
            "properties": {
                "operation_key": {
                    "type": "string",
                    "minLength": 1,
                    "description": "Canonical METHOD /path operation key.",
                }
            },
            "required": ["operation_key"],
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "properties": {
                "operation": {"type": "object"},
                "parameters": {"type": "array"},
                "request_bodies": {"type": "array"},
            },
            "required": ["operation", "parameters", "request_bodies"],
            "additionalProperties": False,
        },
        risk_level="low",
        read_only=True,
        requires_approval=False,
        metadata={"open_world": False},
    )
    registry.register(spec=spec, handler=_lookup_operation)
    return spec


def _lookup_operation(
    context: ToolContext,
    /,
    *,
    operation_key: str,
) -> dict[str, Any]:
    """Return a bounded JSON-ready request contract for one exact operation."""
    operation = context.ir.operations.get(operation_key)
    if operation is None:
        raise OpenAPILookupError(
            f"OpenAPI operation was not found: {operation_key}"
        )
    return {
        "structured": {
            "operation": {
                "operation_key": operation.operation_key,
                "method": operation.method.upper(),
                "path": operation.path,
            },
            "parameters": _operation_parameters(operation),
            "request_bodies": _request_bodies(operation),
        }
    }


def _operation_parameters(operation: OperationIR) -> list[dict[str, Any]]:
    """Project ordinary Parameters and their nested Schema fields."""
    output: list[dict[str, Any]] = []
    for location, parameters in (
        ("path", operation.path_parameters),
        ("query", operation.query_parameters),
        ("header", operation.header_parameters),
        ("cookie", operation.cookie_parameters),
    ):
        for parameter in parameters:
            name = (
                parameter.name.lower()
                if location == "header"
                else parameter.name
            )
            output.extend(
                _schema_parameters(
                    parameter.schema,
                    name=f"{location}.{name}",
                    required=parameter.required,
                    location=location,
                )
            )
    return output


def operation_parameter_handles(operation: OperationIR) -> frozenset[str]:
    """Return every semantic request handle across all request media types.

    Batch generation uses one active media type, but the global OpenAPI tool
    lists every declared body. The run-local Catalog uses this complete set so
    querying a legal field from an inactive media type returns
    ``present: false`` instead of looking forged.
    """
    handles = {
        item["name"]
        for item in _operation_parameters(operation)
    }
    for body in _request_bodies(operation):
        handles.update(
            item["name"]
            for item in body["parameters"]
        )
    return frozenset(handles)


def _request_bodies(operation: OperationIR) -> list[dict[str, Any]]:
    """Keep body fields separated by media type so equal handles stay usable."""
    if operation.request_body is None:
        return []
    return [
        {
            "media_type": media_type,
            "parameters": _schema_parameters(
                media.schema,
                name="body",
                required=operation.request_body.required,
                location="body",
            ),
        }
        for media_type, media in sorted(operation.request_body.contents.items())
        if media.schema is not None
    ]


def _schema_parameters(
    schema: SchemaIR | None,
    *,
    name: str,
    required: bool,
    location: str,
    visited: frozenset[int] = frozenset(),
) -> list[dict[str, Any]]:
    """Flatten one Schema into the semantic handles used by Solve and Patch."""
    if schema is None:
        return [
            {
                "name": name,
                "location": location,
                "required": required,
                "schema": {"type": None, "format": None},
            }
        ]
    item = {
        "name": name,
        "location": location,
        "required": required,
        "schema": _schema_summary(schema),
    }
    identity = id(schema)
    if identity in visited:
        return [item]
    next_visited = visited | {identity}
    output = [item]
    for property_name, child in sorted(schema.properties.items()):
        if child.read_only:
            continue
        output.extend(
            _schema_parameters(
                child,
                name=f"{name}.{property_name}",
                required=property_name in schema.required,
                location=location,
                visited=next_visited,
            )
        )
    if schema.items is not None:
        output.extend(
            _schema_parameters(
                schema.items,
                name=f"{name}[]",
                required=required,
                location=location,
                visited=next_visited,
            )
        )
    for combiner, branches in (
        ("allOf", schema.all_of),
        ("anyOf", schema.any_of),
        ("oneOf", schema.one_of),
    ):
        for index, branch in enumerate(branches):
            output.extend(
                _schema_parameters(
                    branch,
                    name=f"{name}.{combiner}[{index}]",
                    required=required,
                    location=location,
                    visited=next_visited,
                )
            )
    return output


def _schema_summary(schema: SchemaIR) -> dict[str, Any]:
    """Expose constraints useful for Parameter attribution, excluding prose."""
    values = {
        "type": schema.type,
        "format": schema.format,
        "enum": list(schema.enum) if schema.enum is not None else None,
        "const": schema.const,
        "minimum": schema.minimum,
        "maximum": schema.maximum,
        "exclusive_minimum": schema.exclusive_minimum,
        "exclusive_maximum": schema.exclusive_maximum,
        "min_length": schema.min_length,
        "max_length": schema.max_length,
        "pattern": schema.pattern,
        "min_items": schema.min_items,
        "max_items": schema.max_items,
    }
    return {name: value for name, value in values.items() if value is not None}
