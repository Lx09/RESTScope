"""Define the model-visible OpenAPI query contracts.

Each function returns one closed, bounded Tool Schema. Runtime access and
OpenAPI traversal live in sibling modules so contract changes can be reviewed
without reading the query implementation.
"""

from __future__ import annotations

from typing import Any

from restscope.llm import ToolSpec

OPENAPI_LIST_INPUTS_TOOL_NAME = "openapi.list_inputs"
OPENAPI_LIST_OPERATIONS_TOOL_NAME = "openapi.list_operations"
OPENAPI_LIST_RESPONSE_FIELDS_TOOL_NAME = "openapi.list_response_fields"
OPENAPI_FIND_OBSERVED_RESPONSE_FIELDS_TOOL_NAME = (
    "openapi.find_observed_response_fields"
)
OPENAPI_GET_INPUT_SCHEMA_TOOL_NAME = "openapi.get_input_schema"
OPENAPI_GET_RESPONSE_FIELD_SCHEMA_TOOL_NAME = (
    "openapi.get_response_field_schema"
)

_DEFAULT_LIST_LIMIT = 100
_MAX_LIST_LIMIT = 200
_OPERATION_KEY_SCHEMA = {
    "type": "string",
    "minLength": 1,
    "description": (
        "Use an exact RESTScope operation key in METHOD /path format, such as "
        "POST /api/v4/projects. This is not an OpenAPI operationId; do not "
        "convert it to an alias, camelCase, or snake_case variant."
    ),
}


def openapi_list_operations_tool_spec() -> ToolSpec:
    """Describe stable discovery of operations in the current document."""
    return ToolSpec(
        name=OPENAPI_LIST_OPERATIONS_TOOL_NAME,
        description=(
            "List exact RESTScope operation keys from the current OpenAPI "
            "document with method, path, and deprecated status."
        ),
        kind="local_function",
        input_schema={
            "type": "object",
            "properties": {
                "offset": {"type": "integer", "minimum": 0, "default": 0},
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": _MAX_LIST_LIMIT,
                    "default": _DEFAULT_LIST_LIMIT,
                },
            },
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "properties": {
                "operations": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "operation_key": {"type": "string"},
                            "method": {"type": "string"},
                            "path": {"type": "string"},
                            "deprecated": {"type": "boolean"},
                        },
                        "required": ["operation_key", "method", "path", "deprecated"],
                        "additionalProperties": False,
                    },
                },
                "total": {"type": "integer", "minimum": 0},
                "offset": {"type": "integer", "minimum": 0},
                "next_offset": {"type": "integer", "minimum": 0},
            },
            "required": ["operations", "total", "offset"],
            "additionalProperties": False,
        },
    )

def openapi_list_inputs_tool_spec() -> ToolSpec:
    """Describe the paginated request-input discovery tool.

    The result deliberately contains handles rather than Schemas.  An Agent
    that needs one contract can call ``openapi.get_input_schema`` afterward,
    which avoids paying prompt tokens for unrelated inputs.
    """
    return ToolSpec(
        name=OPENAPI_LIST_INPUTS_TOOL_NAME,
        description=(
            "List semantic request-input handles for an exact OpenAPI "
            "operation. Results are paginated and do not include Schemas."
        ),
        kind="local_function",
        input_schema={
            "type": "object",
            "properties": {
                "operation_key": dict(_OPERATION_KEY_SCHEMA),
                "media_type": {"type": "string", "minLength": 1},
                "prefix": {"type": "string", "minLength": 1},
                "offset": {
                    "type": "integer",
                    "minimum": 0,
                    "default": 0,
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": _MAX_LIST_LIMIT,
                    "default": _DEFAULT_LIST_LIMIT,
                },
            },
            "required": ["operation_key"],
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "properties": {
                "operation_key": {"type": "string"},
                "inputs": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "media_type": {"type": "string"},
                        },
                        "required": ["name"],
                        "additionalProperties": False,
                    },
                },
                "total": {"type": "integer", "minimum": 0},
                "offset": {"type": "integer", "minimum": 0},
                "next_offset": {"type": "integer", "minimum": 0},
            },
            "required": ["operation_key", "inputs", "total", "offset"],
            "additionalProperties": False,
        },
    )
def openapi_list_response_fields_tool_spec() -> ToolSpec:
    """Describe the paginated response-field discovery tool.

    The result contains semantic field handles rather than Schemas. A caller
    can pass one returned handle to ``openapi.get_response_field_schema`` when
    it needs the exact contract for that field.
    """
    return ToolSpec(
        name=OPENAPI_LIST_RESPONSE_FIELDS_TOOL_NAME,
        description=(
            "List semantic response-body field handles for one status of an "
            "exact OpenAPI operation. Results are paginated and do not include "
            "Schemas."
        ),
        kind="local_function",
        input_schema={
            "type": "object",
            "properties": {
                "operation_key": dict(_OPERATION_KEY_SCHEMA),
                "status_code": {
                    "oneOf": [
                        {
                            "type": "integer",
                            "minimum": 100,
                            "maximum": 599,
                        },
                        {
                            "type": "string",
                            "pattern": (
                                "^(?:[1-5][0-9]{2}|[1-5][xX]{2}|"
                                "[dD][eE][fF][aA][uU][lL][tT])$"
                            ),
                        },
                    ]
                },
                "offset": {
                    "type": "integer",
                    "minimum": 0,
                    "default": 0,
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": _MAX_LIST_LIMIT,
                    "default": _DEFAULT_LIST_LIMIT,
                },
            },
            "required": ["operation_key", "status_code"],
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "properties": {
                "operation_key": {"type": "string"},
                "requested_status_code": {"type": "string"},
                "matched_status_code": {"type": "string"},
                "media_type": {"type": "string"},
                "fields": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {"name": {"type": "string"}},
                        "required": ["name"],
                        "additionalProperties": False,
                    },
                },
                "total": {"type": "integer", "minimum": 0},
                "offset": {"type": "integer", "minimum": 0},
                "next_offset": {"type": "integer", "minimum": 0},
            },
            "required": [
                "operation_key",
                "requested_status_code",
                "matched_status_code",
                "media_type",
                "fields",
                "total",
                "offset",
            ],
            "additionalProperties": False,
        },
    )


def openapi_find_observed_response_fields_tool_spec() -> ToolSpec:
    """Describe name-based discovery over retained response-field evidence."""
    return ToolSpec(
        name=OPENAPI_FIND_OBSERVED_RESPONSE_FIELDS_TOOL_NAME,
        description=(
            "Find similarly named scalar response fields that have retained "
            "successful-response evidence and still exist in the current "
            "OpenAPI document. Results are paginated by field and grouped by "
            "response contract."
        ),
        kind="local_function",
        input_schema={
            "type": "object",
            "properties": {
                "name": {"type": "string", "minLength": 1, "maxLength": 200},
                "offset": {
                    "type": "integer",
                    "minimum": 0,
                    "default": 0,
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": _MAX_LIST_LIMIT,
                    "default": _DEFAULT_LIST_LIMIT,
                },
            },
            "required": ["name"],
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "properties": {
                "requested_name": {"type": "string"},
                "responses": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "operation_key": {"type": "string"},
                            "matched_status_code": {"type": "string"},
                            "media_type": {"type": "string"},
                            "fields": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "field": {"type": "string"},
                                        "similarity_score": {
                                            "type": "number",
                                            "minimum": 0,
                                            "maximum": 1,
                                        },
                                        "match_basis": {
                                            "type": "string",
                                            "enum": [
                                                "normalized_exact",
                                                "path_exact",
                                                "high_similarity",
                                            ],
                                        },
                                    },
                                    "required": [
                                        "field",
                                        "similarity_score",
                                        "match_basis",
                                    ],
                                    "additionalProperties": False,
                                },
                            },
                        },
                        "required": [
                            "operation_key",
                            "matched_status_code",
                            "media_type",
                            "fields",
                        ],
                        "additionalProperties": False,
                    },
                },
                "total": {"type": "integer", "minimum": 0},
                "offset": {"type": "integer", "minimum": 0},
                "next_offset": {"type": "integer", "minimum": 0},
            },
            "required": ["requested_name", "responses", "total", "offset"],
            "additionalProperties": False,
        },
    )


def openapi_get_input_schema_tool_spec() -> ToolSpec:
    """Describe the exact request-input Schema lookup tool."""
    return ToolSpec(
        name=OPENAPI_GET_INPUT_SCHEMA_TOOL_NAME,
        description=(
            "Return one exact semantic request input's compact Schema, "
            "including its description and example when supplied. Body "
            "inputs may need an explicit media_type."
        ),
        kind="local_function",
        input_schema={
            "type": "object",
            "properties": {
                "operation_key": dict(_OPERATION_KEY_SCHEMA),
                "input": {"type": "string", "minLength": 1},
                "media_type": {"type": "string", "minLength": 1},
            },
            "required": ["operation_key", "input"],
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "properties": {
                "operation_key": {"type": "string"},
                "input": {"type": "string"},
                "location": {
                    "type": "string",
                    "enum": ["path", "query", "header", "cookie", "body"],
                },
                "required": {"type": "boolean"},
                "media_type": {"type": "string"},
                "schema": {
                    "type": "object",
                    "description": (
                        "Bounded OpenAPI Schema keywords for the selected input. "
                        "The keys remain open because OpenAPI permits extensions "
                        "and version-specific Schema keywords."
                    ),
                    "additionalProperties": True,
                },
            },
            "required": [
                "operation_key",
                "input",
                "location",
                "required",
                "schema",
            ],
            "additionalProperties": False,
        },
    )


def openapi_get_response_field_schema_tool_spec() -> ToolSpec:
    """Describe the exact response-body field Schema lookup tool."""
    return ToolSpec(
        name=OPENAPI_GET_RESPONSE_FIELD_SCHEMA_TOOL_NAME,
        description=(
            "Return the compact Schema for one response-body field in an exact "
            "OpenAPI operation. Concrete array indexes are accepted."
        ),
        kind="local_function",
        input_schema={
            "type": "object",
            "properties": {
                "operation_key": dict(_OPERATION_KEY_SCHEMA),
                "status_code": {
                    "oneOf": [
                        {
                            "type": "integer",
                            "minimum": 100,
                            "maximum": 599,
                        },
                        {
                            "type": "string",
                            "pattern": (
                                "^(?:[1-5][0-9]{2}|[1-5][xX]{2}|"
                                "[dD][eE][fF][aA][uU][lL][tT])$"
                            ),
                        },
                    ]
                },
                "field": {
                    "type": "string",
                    "pattern": "^body(?:$|\\.|\\[)",
                },
                "media_type": {"type": "string", "minLength": 1},
            },
            "required": ["operation_key", "status_code", "field"],
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "properties": {
                "operation_key": {"type": "string"},
                "requested_status_code": {"type": "string"},
                "matched_status_code": {"type": "string"},
                "field": {"type": "string"},
                "media_type": {"type": "string"},
                "required": {"type": "boolean"},
                "schema": {
                    "type": "object",
                    "description": (
                        "Bounded OpenAPI Schema keywords for the selected field. "
                        "The keys remain open because OpenAPI permits extensions "
                        "and version-specific Schema keywords."
                    ),
                    "additionalProperties": True,
                },
            },
            "required": [
                "operation_key",
                "requested_status_code",
                "matched_status_code",
                "field",
                "media_type",
                "required",
                "schema",
            ],
            "additionalProperties": False,
        },
    )
