"""Define and bind request-Parameter evidence queries.

The two behaviors read one named Parameter or reverse-search exact typed values
across selected run-local Test Cases.
"""

from __future__ import annotations

from restscope.llm import ToolSpec
from restscope.tools.runtime import ToolBinding

from .contracts import (
    FIND_PARAMETERS_BY_VALUE_TOOL_NAME,
    GET_PARAMETER_VALUE_TOOL_NAME,
    TestCaseToolBackend,
    _ParameterInput,
    _ValueInput,
)
from .execution import _run_catalog_query
from .schemas import _cases_schema, _evidence_fragment_schema, _parameter_fact_schema


def get_parameter_value_tool_spec() -> ToolSpec:
    """Describe exact request-Parameter lookup without an action selector."""
    return ToolSpec(
        name=GET_PARAMETER_VALUE_TOOL_NAME,
        description=(
            "Get one exact request Parameter value for known TC references. "
            "A used result contains direct-name request JSON, while parameter "
            "remains the unique semantic handle. "
            "parameter_not_used_in_request is a final fact for that Test Case; "
            "repeating the same query cannot reveal a value."
        ),
        kind="local_function",
        input_schema=_ParameterInput.model_json_schema(),
        output_schema=_cases_schema(_parameter_fact_schema()),
    )

def find_parameters_by_value_tool_spec() -> ToolSpec:
    """Describe reverse typed-value lookup across request Parameters."""
    return ToolSpec(
        name=FIND_PARAMETERS_BY_VALUE_TOOL_NAME,
        description=(
            "Find request Parameters whose exact typed value matches the supplied "
            "value. Each match contains the unique semantic handle and its "
            "direct-name request JSON fragment."
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
                                "parameter": {"type": "string"},
                                "request": _evidence_fragment_schema("request"),
                            },
                            "required": ["parameter", "request"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["value", "matches"],
                "additionalProperties": False,
            }
        ),
    )


def parameter_tool_bindings(backend: TestCaseToolBackend) -> tuple[ToolBinding, ...]:
    """Bind both Parameter queries to one run-local Test Case backend."""
    return (
        ToolBinding(
            name=GET_PARAMETER_VALUE_TOOL_NAME,
            execute=lambda **arguments: {
                "structured": _run_catalog_query(
                    model_type=_ParameterInput,
                    arguments=arguments,
                    execute=lambda query: backend.get_parameter_value(
                        case_ids=query.case_ids, parameter=query.parameter
                    ),
                )
            },
        ),
        ToolBinding(
            name=FIND_PARAMETERS_BY_VALUE_TOOL_NAME,
            execute=lambda **arguments: {
                "structured": _run_catalog_query(
                    model_type=_ValueInput,
                    arguments=arguments,
                    execute=lambda query: backend.find_parameters_by_value(
                        case_ids=query.case_ids, value=query.value
                    ),
                )
            },
        ),
    )
