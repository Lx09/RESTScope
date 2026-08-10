"""Define inline, run-local outcomes for one generic request Batch.

Batch execution returns canonical request inputs and bounded HTTP or transport
facts directly to its Tool caller. Outcomes have no catalog identity and are
discarded with the Tool result.
"""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _Model(BaseModel):
    """Reject unexpected fields at the bounded inline-outcome seam."""

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


class BatchCaseOutcome(_Model):
    """Carry one canonical request and its bounded transport outcome."""

    case_number: int = Field(ge=1, le=5)
    request: dict[str, Any]
    status_code: int | None = Field(default=None, ge=100, le=599)
    reason_phrase: str | None = Field(default=None, max_length=200)
    failure: CatalogFailure | None = Field(default=None, discriminator="kind")

    @model_validator(mode="after")
    def validate_retained_evidence(self) -> "BatchCaseOutcome":
        """Validate canonical request JSON and transport/HTTP consistency.

        The four ordinary OpenAPI Parameter locations always exist as objects.
        An optional ``body`` key distinguishes an omitted Body from an explicit
        JSON null. No transport-only or internal control fields enter this DTO.
        """
        required_locations = {"path", "query", "header", "cookie"}
        supplied = set(self.request)
        if not required_locations <= supplied:
            missing = sorted(required_locations - supplied)
            raise ValueError(
                "request must contain Parameter location objects: "
                + ", ".join(missing)
            )
        unknown = supplied - required_locations - {"body"}
        if unknown:
            raise ValueError(
                "request contains unknown top-level fields: "
                + ", ".join(sorted(unknown))
            )
        for location in sorted(required_locations):
            if not isinstance(self.request[location], dict):
                raise ValueError(f"request.{location} must be an object")
        try:
            json.dumps(self.request, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "request must contain only JSON-compatible values"
            ) from exc

        if self.status_code is None and not isinstance(self.failure, TransportFailure):
            raise ValueError("a missing HTTP status requires a transport failure")
        if self.status_code is not None and isinstance(self.failure, TransportFailure):
            raise ValueError("a transport failure cannot include an HTTP status")
        return self
