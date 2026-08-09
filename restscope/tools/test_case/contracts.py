"""Define trusted Test Case query ports and validated Tool inputs.

The Tool schemas and bindings share these exact names and Pydantic argument
models. The backend Protocol prevents Tool code from importing the concrete
run-local Test Case Catalog.
"""

from __future__ import annotations

from typing import Any, Protocol, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator


class TestCaseToolBackend(Protocol):
    """Expose only the four run-local evidence queries used by these Tools."""

    def get_parameter_value(
        self, *, case_ids: list[str], parameter: str
    ) -> dict[str, Any]:
        """Return one Parameter fact for each selected Test Case."""

    def find_parameters_by_value(
        self, *, case_ids: list[str], value: Any
    ) -> dict[str, Any]:
        """Find Parameter handles with an exact typed value."""

    def get_response_field_value(
        self, *, case_ids: list[str], field: str
    ) -> dict[str, Any]:
        """Return one response-field fact for each selected Test Case."""

    def find_response_fields_by_value(
        self, *, case_ids: list[str], value: Any
    ) -> dict[str, Any]:
        """Find response fields with an exact typed value."""


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
