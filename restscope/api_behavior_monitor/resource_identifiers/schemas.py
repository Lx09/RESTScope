"""Validated contracts for resource-identifier observation and lookup."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    StrictStr,
    field_validator,
    model_validator,
)

from restscope.data_types import JSONObject, JSONValue


MAX_RESOURCE_NAME_CHARS = 200
MAX_RESOURCE_ALIAS_COUNT = 20
MAX_RESOURCE_SELECTOR_CHARS = 1000
MAX_CLASSIFICATION_GROUPS = 50
MAX_IDENTIFIER_CHARS = 4096

IdentifierValue = Annotated[StrictStr, Field(max_length=MAX_IDENTIFIER_CHARS)] | StrictInt
AccessMode = Literal["read", "write"]
ClassificationSource = Literal["llm"]


class IdentifierFieldMapping(BaseModel):
    """Map one ordered Identifier component to one observed response field."""

    component: str = Field(min_length=1, max_length=MAX_RESOURCE_NAME_CHARS)
    field_name: str = Field(min_length=1, max_length=MAX_RESOURCE_NAME_CHARS)
    selector: str = Field(min_length=1, max_length=MAX_RESOURCE_SELECTOR_CHARS)


class IdentifierComponentValue(BaseModel):
    """Carry one named, typed value inside a complete Identifier Record."""

    name: str = Field(min_length=1, max_length=MAX_RESOURCE_NAME_CHARS)
    value: IdentifierValue
    value_type: Literal["string", "integer"]

    @model_validator(mode="after")
    def require_matching_type(self) -> "IdentifierComponentValue":
        """Prevent a component's declared scalar type from disagreeing with its value."""
        actual = "integer" if isinstance(self.value, int) and not isinstance(self.value, bool) else "string"
        if actual != self.value_type:
            raise ValueError("identifier component value_type does not match value")
        return self


class IdentifierRecord(BaseModel):
    """Represent one complete ordered identifier tuple observed in one item."""

    components: list[IdentifierComponentValue] = Field(min_length=1, max_length=20)

    @field_validator("components")
    @classmethod
    def require_unique_component_names(
        cls, values: list[IdentifierComponentValue]
    ) -> list[IdentifierComponentValue]:
        """Make component order meaningful and component lookup unambiguous."""
        names = [item.name for item in values]
        if len(names) != len(set(names)):
            raise ValueError("identifier component names must be unique")
        return values


class MonitoredOperation(BaseModel):
    """Stable OpenAPI operation identity attached before monitoring."""

    operation_key: str = Field(min_length=1, max_length=MAX_RESOURCE_SELECTOR_CHARS)
    method: str = Field(min_length=1)
    path: str = Field(min_length=1, max_length=MAX_RESOURCE_SELECTOR_CHARS)

    @field_validator("method")
    @classmethod
    def normalize_method(cls, value: str) -> str:
        """Uppercase and validate the HTTP method used for resource operation identity."""
        return value.strip().upper()

    @property
    def access_mode(self) -> AccessMode:
        """Classify the HTTP method as resource read or write usage."""
        return "read" if self.method in {"GET", "HEAD", "OPTIONS"} else "write"


class DetectedResourceGroup(BaseModel):
    """One learned resource with an ordered Identifier Definition and Records."""

    group_path: str = Field(min_length=1, max_length=MAX_RESOURCE_SELECTOR_CHARS)
    has_resource: bool = True
    resource_name: str | None = Field(default=None, max_length=MAX_RESOURCE_NAME_CHARS)
    resource_aliases: list[str] = Field(
        default_factory=list,
        max_length=MAX_RESOURCE_ALIAS_COUNT,
    )
    identifier_name: str | None = Field(default=None, max_length=MAX_RESOURCE_NAME_CHARS)
    identifier_path: str | None = Field(default=None, max_length=MAX_RESOURCE_SELECTOR_CHARS)
    identifier_fields: list[IdentifierFieldMapping] = Field(default_factory=list, max_length=20)
    identifier_records: list[IdentifierRecord] = Field(default_factory=list)
    classification_source: ClassificationSource

    @field_validator("resource_name", "identifier_name")
    @classmethod
    def strip_name(cls, value: str | None) -> str | None:
        """Trim a canonical resource name and reject an empty value."""
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("name cannot be empty")
        return normalized

    @field_validator("resource_aliases")
    @classmethod
    def normalize_aliases(cls, values: list[str]) -> list[str]:
        """Trim, deduplicate, and deterministically sort resource aliases."""
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

    @model_validator(mode="after")
    def require_resource_fields(self) -> "DetectedResourceGroup":
        """Require resource groups to include at least one field and one identifier candidate."""
        required = (
            self.resource_name,
            self.resource_aliases,
            self.identifier_name,
            self.identifier_fields,
        )
        if self.has_resource and any(not item for item in required):
            raise ValueError(
                "resource_name, aliases, id field, and selector are required for a resource"
            )
        if not self.has_resource and (
            any(item for item in required) or self.identifier_path or self.identifier_records
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
    identifier_name: str | None
    identifier_path: str | None
    identifier_fields: list[IdentifierFieldMapping]
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
    identifier: str | None = Field(default=None, max_length=MAX_RESOURCE_NAME_CHARS)
    limit: int = Field(default=20, ge=1, le=100)

    @field_validator("resource")
    @classmethod
    def normalize_resource_query(cls, value: str) -> str:
        """Require at least one resource lookup selector and normalize its method and path."""
        value = value.strip()
        if not value:
            raise ValueError("resource cannot be empty")
        return value


class ResourceIdentifierSummary(BaseModel):
    """Return one complete Identifier Record with its Definition name."""

    identifier: str
    components: list[IdentifierComponentValue]
    last_seen_at: datetime


class ResourceIdentifierPage(BaseModel):
    """Return one bounded typed-ID page without unrelated Monitor evidence.

    ``canonical_resource`` is absent when a query matches no canonical name or
    alias. Identifiers retain their observation time inside the Catalog
    Interface, while a model-facing Capability may deliberately project only
    the value and scalar type.
    """

    status: Literal["found", "not_found"]
    canonical_resource: str | None = None
    identifiers: list[ResourceIdentifierSummary] = Field(default_factory=list)
    total: int = Field(default=0, ge=0)
    offset: int = Field(default=0, ge=0)


class ResourceOperationSummary(BaseModel):
    """Describe the latest observed read or write use of a resource by one operation."""
    operation_key: str
    access_mode: AccessMode
    resource_aliases: list[str]
    id_field_aliases: list[str]
    selectors: list[str]
    latest_seen_at: datetime


class ResourceMonitorErrorSummary(BaseModel):
    """Expose one bounded resource-monitor error without raw response or model data."""
    operation_key: str
    group_path: str
    code: str
    message: str
    issues: list[str]
    updated_at: datetime


class ResourceLookupResult(BaseModel):
    """Return one canonical resource, its bounded identifiers, and operation usage evidence."""
    status: Literal["found", "not_found"]
    canonical_resource: str | None = None
    aliases: list[str] = Field(default_factory=list)
    identifiers: list[ResourceIdentifierSummary] = Field(default_factory=list)
    operations: list[ResourceOperationSummary] = Field(default_factory=list)
    errors: list[ResourceMonitorErrorSummary] = Field(default_factory=list)
    total: int = 0
    truncated: bool = False


class ResourceObservation(BaseModel):
    """Resolved 2xx response passed to Resource Monitor without an OpenAPI IR."""

    operation: MonitoredOperation
    status_code: int = Field(ge=200, le=299)
    media_type: str | None = None
    body: JSONValue
    response_schema_fields: list[JSONObject] = Field(default_factory=list)
    # The Tracker, rather than model construction, turns excess path evidence
    # into a normal Monitor warning so one large OpenAPI document cannot abort
    # the successful target HTTP request that triggered observation.
    related_identifier_paths: tuple[str, ...] = ()
    body_truncated: bool = False


class ResourceMonitorResult(BaseModel):
    """Summarize resources, identifiers, operation usage, and warnings learned from one response."""
    status: Literal["updated", "ignored", "warning"]
    groups_processed: int = 0
    identifiers_recorded: int = 0
    warning: ResourceMonitorWarning | None = None
