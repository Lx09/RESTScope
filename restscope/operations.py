"""Shared operation identity used across RESTScope runtime components."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, model_validator


class OperationReference(BaseModel):
    """Stable method/path identity for one OpenAPI operation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    method: str
    path: str
    operation_id: str | None = None

    @model_validator(mode="after")
    def normalize(self) -> "OperationReference":
        """
        Handle normalize as part of the RESTScope application runtime.

        The class owns any required collaborators or state; arguments supply only the
        data needed for this call.
        """
        object.__setattr__(self, "method", self.method.upper())
        if not self.path.startswith("/"):
            raise ValueError("operation path must start with '/'")
        return self

    def identity(self) -> tuple[str, str, str | None]:
        """
        Handle identity as part of the RESTScope application runtime.

        The class owns any required collaborators or state; arguments supply only the
        data needed for this call.
        """
        return (self.method, self.path, self.operation_id)
