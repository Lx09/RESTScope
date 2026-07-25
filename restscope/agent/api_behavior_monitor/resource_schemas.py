"""Validated contracts for resource-identifier observation and lookup."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    StrictStr,
    field_validator,
    model_validator,
)


MAX_RESOURCE_NAME_CHARS = 200
MAX_RESOURCE_ALIAS_COUNT = 20
MAX_RESOURCE_SELECTOR_CHARS = 1000
MAX_CLASSIFICATION_GROUPS = 50
MAX_IDENTIFIER_CHARS = 4096

IdentifierValue = Annotated[StrictStr, Field(max_length=MAX_IDENTIFIER_CHARS)] | StrictInt
AccessMode = Literal["read", "write"]
ClassificationSource = Literal["exact_id", "llm"]


class MonitoredOperation(BaseModel):
    """Stable OpenAPI operation identity attached before monitoring."""

    operation_key: str = Field(min_length=1, max_length=MAX_RESOURCE_SELECTOR_CHARS)
    method: str = Field(min_length=1)
    path: str = Field(min_length=1, max_length=MAX_RESOURCE_SELECTOR_CHARS)

    @field_validator("method")
    @classmethod
    def normalize_method(cls, value: str) -> str:
        return value.strip().upper()

    @property
    def access_mode(self) -> AccessMode:
        return "read" if self.method in {"GET", "HEAD", "OPTIONS"} else "write"


class DetectedResourceGroup(BaseModel):
    """One learned resource and identifier selector for a response group."""

    group_path: str = Field(min_length=1, max_length=MAX_RESOURCE_SELECTOR_CHARS)
    has_resource: bool = True
    resource_name: str | None = Field(default=None, max_length=MAX_RESOURCE_NAME_CHARS)
    resource_aliases: list[str] = Field(
        default_factory=list,
        max_length=MAX_RESOURCE_ALIAS_COUNT,
    )
    id_field_name: str | None = Field(default=None, max_length=MAX_RESOURCE_NAME_CHARS)
    id_selector: str | None = Field(
        default=None,
        max_length=MAX_RESOURCE_SELECTOR_CHARS,
    )
    identifier_values: list[IdentifierValue] = Field(default_factory=list)
    classification_source: ClassificationSource

    @field_validator("resource_name", "id_field_name")
    @classmethod
    def strip_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("name cannot be empty")
        return normalized

    @field_validator("resource_aliases")
    @classmethod
    def normalize_aliases(cls, values: list[str]) -> list[str]:
        output: list[str] = []
        normalized_seen: set[str] = set()
        for value in values:
            alias = value.strip()
            if not alias:
                raise ValueError("resource alias cannot be empty")
            if len(alias) > MAX_RESOURCE_NAME_CHARS:
                raise ValueError("resource alias is too long")
            key = alias.casefold()
            if key not in normalized_seen:
                normalized_seen.add(key)
                output.append(alias)
        return output

    @field_validator("identifier_values")
    @classmethod
    def validate_identifier_values(
        cls,
        values: list[IdentifierValue],
    ) -> list[IdentifierValue]:
        output: list[IdentifierValue] = []
        seen: set[tuple[type[object], object]] = set()
        for value in values:
            if isinstance(value, str) and not value.strip():
                raise ValueError("string resource identifiers cannot be empty")
            key = (type(value), value)
            if key not in seen:
                seen.add(key)
                output.append(value)
        return output

    @model_validator(mode="after")
    def require_resource_fields(self) -> "DetectedResourceGroup":
        required = (
            self.resource_name,
            self.resource_aliases,
            self.id_field_name,
            self.id_selector,
        )
        if self.has_resource and any(not item for item in required):
            raise ValueError(
                "resource_name, aliases, id field, and selector are required for a resource"
            )
        if not self.has_resource and (
            any(item for item in required) or self.identifier_values
        ):
            raise ValueError("no-resource groups cannot contain resource fields")
        return self


class LearnedResourceRule(BaseModel):
    """Persisted operation/group extraction rule reused without an LLM."""

    rule_id: str
    resource_id: str | None
    has_resource: bool
    resource_name: str | None
    resource_aliases: list[str]
    operation: MonitoredOperation
    group_path: str
    id_field_name: str | None
    id_selector: str | None
    access_mode: AccessMode
    classification_source: ClassificationSource
    id_observed: bool


class ResourceNameSummary(BaseModel):
    """Existing canonical resource and aliases exposed to classification."""

    resource_id: str
    canonical_name: str
    aliases: list[str]


class ResourceMonitorWarning(BaseModel):
    """Bounded failure information that does not replace an HTTP result."""

    code: str = Field(min_length=1)
    message: str = Field(min_length=1)
    issues: list[str] = Field(default_factory=list, max_length=20)


class ResourceLookupRequest(BaseModel):
    """Find reusable identifiers by canonical resource name or alias."""

    model_config = ConfigDict(extra="forbid")

    resource: str = Field(min_length=1, max_length=MAX_RESOURCE_NAME_CHARS)
    id_value: IdentifierValue | None = None
    limit: int = Field(default=20, ge=1, le=100)

    @field_validator("resource")
    @classmethod
    def normalize_resource_query(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("resource cannot be empty")
        return value


class ResourceIdentifierSummary(BaseModel):
    value: IdentifierValue
    value_type: Literal["string", "integer"]
    last_seen_at: datetime


class ResourceOperationSummary(BaseModel):
    operation_key: str
    method: str
    path: str
    access_mode: AccessMode
    resource_aliases: list[str]
    id_field_aliases: list[str]
    selectors: list[str]
    latest_seen_at: datetime


class ResourceMonitorErrorSummary(BaseModel):
    operation_key: str
    group_path: str
    code: str
    message: str
    issues: list[str]
    updated_at: datetime


class ResourceLookupResult(BaseModel):
    status: Literal["found", "not_found"]
    canonical_resource: str | None = None
    aliases: list[str] = Field(default_factory=list)
    identifiers: list[ResourceIdentifierSummary] = Field(default_factory=list)
    recommended_id: IdentifierValue | None = None
    operations: list[ResourceOperationSummary] = Field(default_factory=list)
    errors: list[ResourceMonitorErrorSummary] = Field(default_factory=list)
    total: int = 0
    truncated: bool = False


class ResourceObservation(BaseModel):
    """Resolved 2xx response passed to Resource Monitor without an OpenAPI IR."""

    operation: MonitoredOperation
    status_code: int = Field(ge=200, le=299)
    media_type: str | None = None
    body: Any
    response_schema_fields: list[dict[str, Any]] = Field(default_factory=list)
    body_truncated: bool = False


class ResourceMonitorResult(BaseModel):
    status: Literal["updated", "ignored", "warning"]
    groups_processed: int = 0
    identifiers_recorded: int = 0
    warning: ResourceMonitorWarning | None = None


class ResourceIdentifierSelection(BaseModel):
    """One bounded model choice mapped back to local identifier evidence."""

    model_config = ConfigDict(extra="forbid")

    identifier_candidate_id: str | None = Field(
        default=None,
        max_length=100,
    )

    @field_validator("identifier_candidate_id")
    @classmethod
    def reject_blank_candidate_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("identifier candidate id cannot be blank")
        return normalized
