"""Bind authorized OpenAPI Tool names to the App-scoped query backend.

The Harness uses these helpers after Profile validation. They add no discovery
or authorization; they only connect already-selected names to trusted methods.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from restscope.tools.runtime import ToolBinding

from .backend import OpenAPIToolBackend
from .specs import (
    OPENAPI_FIND_OBSERVED_RESPONSE_FIELDS_TOOL_NAME,
    OPENAPI_GET_INPUT_SCHEMA_TOOL_NAME,
    OPENAPI_GET_RESPONSE_FIELD_SCHEMA_TOOL_NAME,
    OPENAPI_LIST_INPUTS_TOOL_NAME,
    OPENAPI_LIST_OPERATIONS_TOOL_NAME,
    OPENAPI_LIST_RESPONSE_FIELDS_TOOL_NAME,
)

def openapi_tool_bindings(
    backend: "OpenAPIToolBackend",
    *,
    names: set[str] | None = None,
) -> tuple[ToolBinding, ...]:
    """Bind selected OpenAPI Tools to one App-scoped read implementation."""
    method_by_name = (
        (OPENAPI_LIST_OPERATIONS_TOOL_NAME, "list_operations"),
        (OPENAPI_LIST_INPUTS_TOOL_NAME, "list_inputs"),
        (OPENAPI_LIST_RESPONSE_FIELDS_TOOL_NAME, "list_response_fields"),
        (
            OPENAPI_FIND_OBSERVED_RESPONSE_FIELDS_TOOL_NAME,
            "find_observed_response_fields",
        ),
        (OPENAPI_GET_INPUT_SCHEMA_TOOL_NAME, "get_input_schema"),
        (
            OPENAPI_GET_RESPONSE_FIELD_SCHEMA_TOOL_NAME,
            "get_response_field_schema",
        ),
    )
    selected = names or {name for name, _method in method_by_name}
    return tuple(
        ToolBinding(name=name, execute=getattr(backend, method))
        for name, method in method_by_name
        if name in selected
    )

def observed_response_fields_tool_binding(
    backend: "OpenAPIToolBackend | None",
    *,
    unavailable: Callable[..., dict[str, Any]],
) -> ToolBinding:
    """Bind the Patch-facing observed-field lookup or its safe unavailable result."""
    return ToolBinding(
        name=OPENAPI_FIND_OBSERVED_RESPONSE_FIELDS_TOOL_NAME,
        execute=(
            backend.find_observed_response_fields
            if backend is not None
            else unavailable
        ),
    )
