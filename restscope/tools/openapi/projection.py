"""Project OpenAPI Schema nodes into bounded model-visible summaries.

The OpenAPI backend passes only one selected Schema into these helpers. They
retain constraints and examples useful for diagnosis while bounding nested
values, enum size, and untrusted text.
"""

from __future__ import annotations

from typing import Any

from restscope.openapi_parser.ir import SchemaIR

_MAX_ENUM_VALUES = 50
_MAX_SCHEMA_TEXT_CHARS = 800

def _schema_summary(schema: SchemaIR | None) -> dict[str, Any]:
    """Return only one node's bounded structural and validation facts."""
    if schema is None:
        return {}
    enum_values = list(schema.enum) if schema.enum is not None else None
    values: dict[str, Any] = {
        "type": schema.type,
        "format": schema.format,
        "nullable": schema.nullable,
        "enum": (
            [_bound_schema_value(item) for item in enum_values[:_MAX_ENUM_VALUES]]
            if enum_values is not None
            else None
        ),
        "enum_count": len(enum_values) if enum_values is not None else None,
        "enum_truncated": (
            len(enum_values) > _MAX_ENUM_VALUES
            if enum_values is not None
            else None
        ),
        "const": _bound_schema_value(schema.const),
        "minimum": schema.minimum,
        "maximum": schema.maximum,
        "exclusive_minimum": schema.exclusive_minimum,
        "exclusive_maximum": schema.exclusive_maximum,
        "min_length": schema.min_length,
        "max_length": schema.max_length,
        "pattern": _bound_schema_value(schema.pattern),
        "min_items": schema.min_items,
        "max_items": schema.max_items,
        "unique_items": schema.unique_items,
        "min_properties": schema.min_properties,
        "max_properties": schema.max_properties,
        "additional_properties": _additional_properties_summary(
            schema.additional_properties
        ),
    }
    return {name: value for name, value in values.items() if value is not None}


def _input_schema_summary(schema: SchemaIR | None) -> dict[str, Any]:
    """Add request-input guidance to one exact, bounded Schema summary.

    Descriptions and examples help Failure Resolution interpret an otherwise terse
    contract. They remain bounded because both fields originate in the
    caller-supplied OpenAPI document and may contain large or hostile values.
    """
    summary = _schema_summary(schema)
    if schema is None:
        return summary
    if schema.description is not None:
        summary["description"] = _bound_schema_value(schema.description)
    if schema.example is not None:
        summary["example"] = _bound_schema_value(schema.example)
    return summary


def _additional_properties_summary(value: bool | SchemaIR | None) -> Any | None:
    """Describe whether an object accepts undeclared fields without a subtree."""
    if isinstance(value, SchemaIR):
        # The selected node owns only the shape of additional values. Returning
        # their full subtree would defeat exact-node lookup and could recurse
        # forever for a circular Schema.
        return {
            "schema": {
                name: item
                for name, item in {
                    "type": value.type,
                    "format": value.format,
                    "nullable": value.nullable,
                }.items()
                if item is not None
            }
        }
    return value

def _bound_schema_value(value: Any, *, depth: int = 0) -> Any:
    """Bound untrusted Schema literals so one exact query stays token-safe."""
    if isinstance(value, str):
        if len(value) <= _MAX_SCHEMA_TEXT_CHARS:
            return value
        return {
            "truncated": True,
            "original_chars": len(value),
            "value": value[:_MAX_SCHEMA_TEXT_CHARS],
        }
    if depth >= 3:
        return {"truncated": True, "type": type(value).__name__}
    if isinstance(value, list):
        retained = value[:_MAX_ENUM_VALUES]
        output = [_bound_schema_value(item, depth=depth + 1) for item in retained]
        if len(value) > len(retained):
            output.append({"truncated_items": len(value) - len(retained)})
        return output
    if isinstance(value, dict):
        items = list(value.items())[:_MAX_ENUM_VALUES]
        output = {
            str(name): _bound_schema_value(item, depth=depth + 1)
            for name, item in items
        }
        if len(value) > len(items):
            output["<truncated_properties>"] = len(value) - len(items)
        return output
    return value
