"""Expose single-purpose model tools for run-local Test Case evidence.

Failure Resolution receives four registered tools for request and response
investigation. The already-rendered Failure messages have a retained lookup
spec for other callers but are deliberately not registered for Resolution.
Each tool names one exact Catalog query, validates only the arguments needed by
that query, and returns bounded native JSON. The shared
:class:`TestCaseCatalog` remains responsible for storage, Test Case identity,
typed comparison, and response path traversal.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any, TypeVar

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
)

from restscope.capabilities import AgentToolbox, ToolFailure
from restscope.llm import ToolResult, ToolSpec

from .catalog import TestCaseCatalog


GET_PARAMETER_VALUE_TOOL_NAME = "test_case.get_parameter_value"
FIND_PARAMETERS_BY_VALUE_TOOL_NAME = "test_case.find_parameters_by_value"
GET_RESPONSE_FIELD_VALUE_TOOL_NAME = "test_case.get_response_field_value"
FIND_RESPONSE_FIELDS_BY_VALUE_TOOL_NAME = (
    "test_case.find_response_fields_by_value"
)
GET_FAILURE_MESSAGES_TOOL_NAME = "test_case.get_failure_messages"
TEST_CASE_TOOL_NAMES = frozenset(
    {
        GET_PARAMETER_VALUE_TOOL_NAME,
        FIND_PARAMETERS_BY_VALUE_TOOL_NAME,
        GET_RESPONSE_FIELD_VALUE_TOOL_NAME,
        FIND_RESPONSE_FIELDS_BY_VALUE_TOOL_NAME,
    }
)

_MAX_TOOL_VALUE_CHARS = 1_200


class _ToolInput(BaseModel):
    """Reject arguments not declared by one single-purpose Catalog tool."""

    model_config = ConfigDict(extra="forbid")


class _CaseIdsInput(_ToolInput):
    """Validate the bounded same-query Test Case batch shared by every tool."""

    case_ids: list[str] = Field(
        min_length=1,
        max_length=20,
        json_schema_extra={"uniqueItems": True},
        description="Unique run-local TC references to inspect together.",
    )

    @field_validator("case_ids")
    @classmethod
    def require_unique_case_ids(cls, value: list[str]) -> list[str]:
        """Reject duplicate references before the Catalog repeats any work."""
        if len(value) != len(set(value)):
            raise ValueError("case_ids must be unique")
        return value


class _ParameterInput(_CaseIdsInput):
    """Select one semantic request Parameter across known Test Cases."""

    parameter: str = Field(
        min_length=1,
        description=(
            "Exact semantic input handle supplied by the owning Agent, for "
            "example query.sort. Test Case request JSON uses the direct key sort "
            "inside its query object."
        ),
    )


class _ResponseFieldInput(_CaseIdsInput):
    """Select one concrete path inside each retained failed response body."""

    field: str = Field(
        min_length=1,
        description=(
            "Concrete response path beginning with body, for example "
            "body.message or body.errors[0].code."
        ),
    )


class _ValueInput(_CaseIdsInput):
    """Select one exact typed JSON-like value for reverse lookup."""

    value: Any


_InputT = TypeVar("_InputT", bound=_ToolInput)


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
                    "value": {},
                    "matches": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "parameter": {"type": "string"},
                                "request": {"type": "object"},
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
                    "value": {},
                    "matches": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "field": {"type": "string"},
                                "response": {"type": "object"},
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


def get_failure_messages_tool_spec() -> ToolSpec:
    """Describe parsed Failure-message lookup for exact Test Cases."""
    return ToolSpec(
        name=GET_FAILURE_MESSAGES_TOOL_NAME,
        description=(
            "Get parsed Failure messages for known TC references. An empty list "
            "means that Test Case has no retained Failure messages."
        ),
        kind="local_function",
        input_schema=_CaseIdsInput.model_json_schema(),
        output_schema=_cases_schema(
            {
                "type": "object",
                "properties": {
                    "messages": {"type": "array", "items": {}},
                },
                "required": ["messages"],
                "additionalProperties": False,
            }
        ),
    )


def register_test_case_tools(
    *,
    toolbox: AgentToolbox,
    catalog: TestCaseCatalog,
) -> None:
    """Register the four Catalog reads exposed to Failure Resolution.

    Args:
        toolbox: The Resolution-owned tool Module to extend.
        catalog: The run-local evidence store used by every registered query.

    This adapter keeps Resolution's registered queries on the Catalog's exact
    contracts. It changes only the supplied toolbox and never changes Catalog
    evidence.
    """
    toolbox.register(
        spec=get_parameter_value_tool_spec(),
        execute=lambda **arguments: {
            "structured": _run_catalog_query(
                model_type=_ParameterInput,
                arguments=arguments,
                execute=lambda query: catalog.get_parameter_value(
                    case_ids=query.case_ids,
                    parameter=query.parameter,
                ),
            )
        },
    )
    toolbox.register(
        spec=find_parameters_by_value_tool_spec(),
        execute=lambda **arguments: {
            "structured": _run_catalog_query(
                model_type=_ValueInput,
                arguments=arguments,
                execute=lambda query: catalog.find_parameters_by_value(
                    case_ids=query.case_ids,
                    value=query.value,
                ),
            )
        },
    )
    toolbox.register(
        spec=get_response_field_value_tool_spec(),
        execute=lambda **arguments: {
            "structured": _run_catalog_query(
                model_type=_ResponseFieldInput,
                arguments=arguments,
                execute=lambda query: catalog.get_response_field_value(
                    case_ids=query.case_ids,
                    field=query.field,
                ),
            )
        },
    )
    toolbox.register(
        spec=find_response_fields_by_value_tool_spec(),
        execute=lambda **arguments: {
            "structured": _run_catalog_query(
                model_type=_ValueInput,
                arguments=arguments,
                execute=lambda query: catalog.find_response_fields_by_value(
                    case_ids=query.case_ids,
                    value=query.value,
                ),
            )
        },
    )


def _run_catalog_query(
    *,
    model_type: type[_InputT],
    arguments: dict[str, Any],
    execute: Callable[[_InputT], dict[str, Any]],
) -> dict[str, Any]:
    """Validate one fixed query contract and bound its model-visible result."""
    try:
        query = model_type.model_validate(arguments)
        return _bound_catalog_result(execute(query))
    except (ValidationError, KeyError, ValueError) as exc:
        raise ToolFailure(
            code="invalid_test_case_query",
            message=str(exc),
        ) from exc


def tool_result_json(result: ToolResult) -> str:
    """Serialize a native structured tool response without Markdown wrapping."""
    return json.dumps(
        result.model_dump(mode="json", exclude_none=True),
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _cases_schema(fact_schema: dict[str, Any]) -> dict[str, Any]:
    """Build the shared case-keyed output envelope for one fixed fact shape."""
    return {
        "type": "object",
        "properties": {
            "cases": {
                "type": "object",
                "additionalProperties": fact_schema,
            }
        },
        "required": ["cases"],
        "additionalProperties": False,
    }


def _parameter_fact_schema() -> dict[str, Any]:
    """Describe used and unused Parameter facts without a boolean flag."""
    return {
        "oneOf": [
            {
                "type": "object",
                "properties": {
                    "parameter": {"type": "string"},
                    "status": {"const": "parameter_used_in_request"},
                    "request": {"type": "object"},
                },
                "required": ["parameter", "status", "request"],
                "additionalProperties": False,
            },
            {
                "type": "object",
                "properties": {
                    "parameter": {"type": "string"},
                    "status": {"const": "parameter_not_used_in_request"},
                },
                "required": ["parameter", "status"],
                "additionalProperties": False,
            },
        ]
    }


def _response_field_fact_schema() -> dict[str, Any]:
    """Describe the three exact response-field evidence outcomes."""
    statuses_without_value = [
        "response_body_not_retained",
        "response_field_not_present_in_retained_body",
    ]
    return {
        "oneOf": [
            {
                "type": "object",
                "properties": {
                    "field": {"type": "string"},
                    "status": {
                        "const": "response_field_present_in_retained_body"
                    },
                    "response": {"type": "object"},
                },
                "required": ["field", "status", "response"],
                "additionalProperties": False,
            },
            {
                "type": "object",
                "properties": {
                    "field": {"type": "string"},
                    "status": {"enum": statuses_without_value},
                },
                "required": ["field", "status"],
                "additionalProperties": False,
            },
        ]
    }


def _bound_tool_values(value: Any) -> Any:
    """Clip one selected scalar or container while retaining its original size."""
    if isinstance(value, str) and len(value) > _MAX_TOOL_VALUE_CHARS:
        retained = _MAX_TOOL_VALUE_CHARS - 200
        head = retained // 2
        tail = retained - head
        return {
            "truncated": True,
            "type": "string",
            "original_chars": len(value),
            "value": value[:head] + "…" + value[-tail:],
        }
    if isinstance(value, bytes):
        if len(value) <= _MAX_TOOL_VALUE_CHARS:
            return {
                "type": "bytes",
                "hex": value.hex(),
                "length": len(value),
            }
        retained = (_MAX_TOOL_VALUE_CHARS - 200) // 2
        return {
            "truncated": True,
            "type": "bytes",
            "original_bytes": len(value),
            "head_hex": value[:retained].hex(),
            "tail_hex": value[-retained:].hex(),
        }
    if isinstance(value, dict):
        bounded = {
            str(name): _bound_tool_values(child)
            for name, child in value.items()
        }
        return _clip_container(bounded, kind="object")
    if isinstance(value, list):
        bounded = [_bound_tool_values(child) for child in value]
        return _clip_container(bounded, kind="array")
    return value


def _bound_catalog_result(result: dict[str, Any]) -> dict[str, Any]:
    """Bound every selected fact while preserving the case-keyed envelope."""
    cases = result.get("cases")
    assert isinstance(cases, dict)
    return {
        "cases": {
            case_id: {
                name: _bound_tool_values(value)
                for name, value in facts.items()
            }
            for case_id, facts in cases.items()
        }
    }


def _clip_container(value: Any, *, kind: str) -> Any:
    """Replace a large object/array value with a typed head/tail JSON preview."""
    rendered = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    if len(rendered) <= _MAX_TOOL_VALUE_CHARS:
        return value
    retained = _MAX_TOOL_VALUE_CHARS - 240
    head = retained // 2
    tail = retained - head
    return {
        "truncated": True,
        "type": kind,
        "original_chars": len(rendered),
        "head": rendered[:head],
        "tail": rendered[-tail:],
    }
