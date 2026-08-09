"""Define and bind retained response-field evidence queries.

The two behaviors read one concrete body path or reverse-search exact typed
values across the bounded failed responses retained for selected Test Cases.
"""

from __future__ import annotations

from restscope.llm import ToolSpec
from restscope.tools.runtime import ToolBinding

from .contracts import (
    FIND_RESPONSE_FIELDS_BY_VALUE_TOOL_NAME,
    GET_RESPONSE_FIELD_VALUE_TOOL_NAME,
    TestCaseToolBackend,
    _ResponseFieldInput,
    _ValueInput,
)
from .execution import _run_catalog_query
from .schemas import _cases_schema, _evidence_fragment_schema, _response_field_fact_schema


def get_response_field_value_tool_spec() -> ToolSpec:
    """Describe exact retained-response-field lookup with explicit absence."""
    return ToolSpec(
        name=GET_RESPONSE_FIELD_VALUE_TOOL_NAME,
        description=(
            "Get one concrete field from each retained failed response body. "
            "A present result contains response.body JSON with direct field names. "
            "The result distinguishes an unretained body from a retained body "
            "without that field; either status is final for the same TC and field."
        ),
        kind="local_function",
        input_schema=_ResponseFieldInput.model_json_schema(),
        output_schema=_cases_schema(_response_field_fact_schema()),
    )

def find_response_fields_by_value_tool_spec() -> ToolSpec:
    """Describe reverse typed-value lookup across retained response fields."""
    return ToolSpec(
        name=FIND_RESPONSE_FIELDS_BY_VALUE_TOOL_NAME,
        description=(
            "Find concrete body paths whose exact typed value matches the supplied "
            "value. Each match contains the unique field path and a response.body "
            "JSON fragment with direct field names."
        ),
        kind="local_function",
        input_schema=_ValueInput.model_json_schema(),
        output_schema=_cases_schema(
            {
                "type": "object",
                "properties": {
                    "value": {
                        "description": (
                            "Exact typed JSON-like value supplied for reverse lookup."
                        )
                    },
                    "matches": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "field": {"type": "string"},
                                "response": _evidence_fragment_schema("response"),
                            },
                            "required": ["field", "response"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["value", "matches"],
                "additionalProperties": False,
            }
        ),
    )


def response_tool_bindings(backend: TestCaseToolBackend) -> tuple[ToolBinding, ...]:
    """Bind both retained-response queries to one run-local backend."""
    return (
        ToolBinding(
            name=GET_RESPONSE_FIELD_VALUE_TOOL_NAME,
            execute=lambda **arguments: {
                "structured": _run_catalog_query(
                    model_type=_ResponseFieldInput,
                    arguments=arguments,
                    execute=lambda query: backend.get_response_field_value(
                        case_ids=query.case_ids, field=query.field
                    ),
                )
            },
        ),
        ToolBinding(
            name=FIND_RESPONSE_FIELDS_BY_VALUE_TOOL_NAME,
            execute=lambda **arguments: {
                "structured": _run_catalog_query(
                    model_type=_ValueInput,
                    arguments=arguments,
                    execute=lambda query: backend.find_response_fields_by_value(
                        case_ids=query.case_ids, value=query.value
                    ),
                )
            },
        ),
    )
