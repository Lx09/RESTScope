"""Define run-local Test Case Catalog values and query commands.

Batch execution and Solve HTTP probes produce ``CatalogTestCaseDraft`` values.
The Catalog assigns short ``TC*`` identities and returns immutable
``CatalogTestCase`` records. Agents never receive these DTOs wholesale; they
submit a bounded ``CatalogQuery`` and receive only the requested facts.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _Model(BaseModel):
    """Reject unexpected fields at the in-memory Catalog seam."""

    model_config = ConfigDict(extra="forbid")


class HTTPFailure(_Model):
    """Retain one non-2xx HTTP outcome and its parsed Failure messages."""

    kind: Literal["http"] = "http"
    status_code: int = Field(ge=100, le=599)
    messages: list[str] = Field(min_length=1)
    body_truncated: bool = False


class TransportFailure(_Model):
    """Retain a request attempt that ended before an HTTP response existed."""

    kind: Literal["transport"] = "transport"
    code: str = Field(min_length=1)
    messages: list[str] = Field(min_length=1)


CatalogFailure = HTTPFailure | TransportFailure


class CatalogTestCaseDraft(_Model):
    """Carry one executed request before the Catalog assigns its short identity."""

    parameters: dict[str, Any]
    response_body: Any | None = None
    failure: CatalogFailure | None = Field(default=None, discriminator="kind")

    @model_validator(mode="after")
    def validate_response_retention(self) -> "CatalogTestCaseDraft":
        """Permit a body only for the approved actionable HTTP status range."""
        if self.response_body is None:
            return self
        if (
            not isinstance(self.failure, HTTPFailure)
            or not 400 <= self.failure.status_code < 600
        ):
            raise ValueError(
                "response_body may be retained only for a 4xx/5xx HTTP Failure"
            )
        return self


class CatalogTestCase(CatalogTestCaseDraft):
    """Represent one immutable Test Case stored for the current Smoke run."""

    case_id: str = Field(pattern=r"^TC[1-9][0-9]*$")


CatalogQueryAction = Literal[
    "parameter_value",
    "response_field_value",
    "parameters_using_value",
    "response_fields_using_value",
    "failure_messages",
]


class CatalogQuery(_Model):
    """Describe one exact, bounded read against one or more Test Cases."""

    action: CatalogQueryAction
    case_ids: list[str] = Field(min_length=1, max_length=20)
    name: str | None = Field(default=None, min_length=1)
    value: Any | None = None

    @model_validator(mode="after")
    def validate_action_arguments(self) -> "CatalogQuery":
        """Require only the selector used by the chosen query action."""
        needs_name = self.action in {
            "parameter_value",
            "response_field_value",
        }
        needs_value = self.action in {
            "parameters_using_value",
            "response_fields_using_value",
        }
        if needs_name != (self.name is not None):
            raise ValueError(
                f"{self.action} "
                + ("requires name" if needs_name else "does not accept name")
            )
        supplied_value = "value" in self.model_fields_set
        if needs_value != supplied_value:
            raise ValueError(
                f"{self.action} "
                + ("requires value" if needs_value else "does not accept value")
            )
        if len(self.case_ids) != len(set(self.case_ids)):
            raise ValueError("case_ids must be unique")
        return self
