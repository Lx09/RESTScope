"""Public contracts for configured input generation and execution."""

from __future__ import annotations

from datetime import datetime
import re
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _Strategy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ConstantGenerator(_Strategy):
    """
    Represent the constant generator expression used by deterministic request
    generation, constraint solving, and execution.

    Its fields describe data only; generation or evaluation behavior lives in the
    corresponding service functions.
    """
    type: Literal["constant"]
    value: Any


class ChoiceGenerator(_Strategy):
    """
    Represent the choice generator expression used by deterministic request generation,
    constraint solving, and execution.

    Its fields describe data only; generation or evaluation behavior lives in the
    corresponding service functions.
    """
    type: Literal["choice"]
    values: list[Any] = Field(min_length=1)
    weights: list[float] | None = None

    @model_validator(mode="after")
    def validate_weights(self) -> "ChoiceGenerator":
        """
        Validate weights for deterministic request generation, constraint solving, and
        execution.

        The class owns any required collaborators or state; arguments supply only the
        data needed for this call.
        """
        if self.weights is not None:
            if len(self.weights) != len(self.values):
                raise ValueError("weights must have the same length as values")
            if any(weight < 0 for weight in self.weights) or not any(self.weights):
                raise ValueError("weights must be non-negative with at least one positive value")
        return self


class IntegerRangeGenerator(_Strategy):
    """
    Represent the integer range generator expression used by deterministic request
    generation, constraint solving, and execution.

    Its fields describe data only; generation or evaluation behavior lives in the
    corresponding service functions.
    """
    type: Literal["integer_range"]
    minimum: int
    maximum: int

    @model_validator(mode="after")
    def validate_range(self) -> "IntegerRangeGenerator":
        """
        Validate range for deterministic request generation, constraint solving, and
        execution.

        The class owns any required collaborators or state; arguments supply only the
        data needed for this call.
        """
        if self.minimum > self.maximum:
            raise ValueError("minimum cannot exceed maximum")
        return self


class NumberRangeGenerator(_Strategy):
    """
    Represent the number range generator expression used by deterministic request
    generation, constraint solving, and execution.

    Its fields describe data only; generation or evaluation behavior lives in the
    corresponding service functions.
    """
    type: Literal["number_range"]
    minimum: float
    maximum: float

    @model_validator(mode="after")
    def validate_range(self) -> "NumberRangeGenerator":
        """
        Validate range for deterministic request generation, constraint solving, and
        execution.

        The class owns any required collaborators or state; arguments supply only the
        data needed for this call.
        """
        if self.minimum > self.maximum:
            raise ValueError("minimum cannot exceed maximum")
        return self


class RandomStringGenerator(_Strategy):
    """
    Represent the random string generator expression used by deterministic request
    generation, constraint solving, and execution.

    Its fields describe data only; generation or evaluation behavior lives in the
    corresponding service functions.
    """
    type: Literal["random_string"]
    min_length: int = Field(default=1, ge=0)
    max_length: int = Field(default=16, ge=0)
    alphabet: str = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"

    @model_validator(mode="after")
    def validate_string_options(self) -> "RandomStringGenerator":
        """
        Validate string options for deterministic request generation, constraint
        solving, and execution.

        The class owns any required collaborators or state; arguments supply only the
        data needed for this call.
        """
        if self.min_length > self.max_length:
            raise ValueError("min_length cannot exceed max_length")
        if not self.alphabet and self.max_length:
            raise ValueError("alphabet cannot be empty when generated strings may be non-empty")
        return self


class RegexGenerator(_Strategy):
    """Generate a bounded string that contains a Python regular-expression match.

    ``pattern`` uses the same ``re.search`` meaning as frozen OpenAPI patterns:
    an unanchored expression may match only part of the returned string.
    ``min_length`` and ``max_length`` bound the whole returned string so a
    configured expression cannot create unbounded request data.
    """

    type: Literal["regex"]
    pattern: str = Field(max_length=2000)
    min_length: int = Field(default=0, ge=0, le=10_000)
    max_length: int = Field(default=100, ge=0, le=10_000)

    @model_validator(mode="after")
    def validate_regex_options(self) -> "RegexGenerator":
        """Return this contract after validating its cross-field boundaries.

        The method changes no state. It raises a validation error when the
        length interval is reversed or Python cannot compile ``pattern``.
        """

        if self.min_length > self.max_length:
            raise ValueError("min_length cannot exceed max_length")
        try:
            re.compile(self.pattern)
        except re.error as exc:
            raise ValueError("pattern must be a valid regular expression") from exc
        return self


class BooleanGenerator(_Strategy):
    """
    Represent the boolean generator expression used by deterministic request generation,
    constraint solving, and execution.

    Its fields describe data only; generation or evaluation behavior lives in the
    corresponding service functions.
    """
    type: Literal["boolean"]
    true_probability: float = Field(default=0.5, ge=0, le=1)


class FormatGenerator(_Strategy):
    """
    Represent the format generator expression used by deterministic request generation,
    constraint solving, and execution.

    Its fields describe data only; generation or evaluation behavior lives in the
    corresponding service functions.
    """
    type: Literal["format"]
    format: Literal["uuid", "date", "date-time", "email"]


class ObjectGenerator(_Strategy):
    """
    Represent the object generator expression used by deterministic request generation,
    constraint solving, and execution.

    Its fields describe data only; generation or evaluation behavior lives in the
    corresponding service functions.
    """
    type: Literal["object"]


class ArrayGenerator(_Strategy):
    """
    Represent the array generator expression used by deterministic request generation,
    constraint solving, and execution.

    Its fields describe data only; generation or evaluation behavior lives in the
    corresponding service functions.
    """
    type: Literal["array"]
    min_items: int = Field(default=1, ge=0)
    max_items: int = Field(default=1, ge=0)

    @model_validator(mode="after")
    def validate_length(self) -> "ArrayGenerator":
        """
        Validate length for deterministic request generation, constraint solving, and
        execution.

        The class owns any required collaborators or state; arguments supply only the
        data needed for this call.
        """
        if self.min_items > self.max_items:
            raise ValueError("min_items cannot exceed max_items")
        return self


class VariantGenerator(_Strategy):
    """
    Represent the variant generator expression used by deterministic request generation,
    constraint solving, and execution.

    Its fields describe data only; generation or evaluation behavior lives in the
    corresponding service functions.
    """
    type: Literal["variant"]
    branch_weights: list[float] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_branch_weights(self) -> "VariantGenerator":
        """
        Validate branch weights for deterministic request generation, constraint
        solving, and execution.

        The class owns any required collaborators or state; arguments supply only the
        data needed for this call.
        """
        if any(weight < 0 for weight in self.branch_weights) or not any(self.branch_weights):
            raise ValueError(
                "branch_weights must be non-negative with at least one positive value"
            )
        return self


class RequestBodyGenerator(_Strategy):
    """
    Represent the request body generator expression used by deterministic request
    generation, constraint solving, and execution.

    Its fields describe data only; generation or evaluation behavior lives in the
    corresponding service functions.
    """
    type: Literal["request_body"]


class ResourceIdentifierGenerator(_Strategy):
    """Select an identifier already observed for one canonical resource."""

    type: Literal["resource_identifier"]
    resource: str = Field(min_length=1, max_length=200)


class ResponseValueGenerator(_Strategy):
    """Select a deduplicated response value from one named monitor pool."""

    type: Literal["response_value"]
    value_name: str = Field(min_length=1, max_length=200)


GeneratorStrategy = Annotated[
    ConstantGenerator
    | ChoiceGenerator
    | IntegerRangeGenerator
    | NumberRangeGenerator
    | RandomStringGenerator
    | RegexGenerator
    | BooleanGenerator
    | FormatGenerator
    | ObjectGenerator
    | ArrayGenerator
    | VariantGenerator
    | ResourceIdentifierGenerator
    | ResponseValueGenerator
    | RequestBodyGenerator,
    Field(discriminator="type"),
]


class InputGeneratorConfig(BaseModel):
    """One persisted strategy bound to a stable IR input node."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    input_node_id: str
    inclusion_probability: float = Field(ge=0, le=1)
    strategy: GeneratorStrategy


class InputGeneratorPatch(BaseModel):
    """Partial update for one frozen input generator."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    input_node_id: str
    inclusion_probability: float | None = Field(default=None, ge=0, le=1)
    strategy: GeneratorStrategy | None = None

    @model_validator(mode="after")
    def validate_change(self) -> "InputGeneratorPatch":
        """
        Validate change for deterministic request generation, constraint solving, and
        execution.

        The class owns any required collaborators or state; arguments supply only the
        data needed for this call.
        """
        if self.inclusion_probability is None and self.strategy is None:
            raise ValueError("generator patch must change strategy or inclusion_probability")
        return self


class SchemaSnapshot(BaseModel):
    """JSON-safe generation constraints frozen during first initialization."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    type: str | list[str] | None = None
    format: str | None = None
    properties: dict[str, "SchemaSnapshot"] = Field(default_factory=dict)
    read_only_properties: list[str] = Field(default_factory=list)
    required: list[str] = Field(default_factory=list)
    items: "SchemaSnapshot | None" = None
    enum: list[Any] | None = None
    const: Any | None = None
    has_const: bool = False
    default: Any | None = None
    has_default: bool = False
    example: Any | None = None
    has_example: bool = False
    nullable: bool | None = None
    minimum: int | float | None = None
    maximum: int | float | None = None
    exclusive_minimum: int | float | bool | None = None
    exclusive_maximum: int | float | bool | None = None
    multiple_of: int | float | None = None
    min_length: int | None = None
    max_length: int | None = None
    pattern: str | None = None
    min_items: int | None = None
    max_items: int | None = None
    unique_items: bool | None = None
    min_properties: int | None = None
    max_properties: int | None = None
    additional_properties: "bool | SchemaSnapshot | None" = None
    all_of: list["SchemaSnapshot"] = Field(default_factory=list)
    any_of: list["SchemaSnapshot"] = Field(default_factory=list)
    one_of: list["SchemaSnapshot"] = Field(default_factory=list)
    has_not: bool = False
    has_conditional: bool = False


class ParameterSnapshot(BaseModel):
    """Frozen OpenAPI parameter serialization contract."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    input_node_id: str
    name: str
    location: Literal["path", "query", "header", "cookie"]
    required: bool
    style: str | None = None
    explode: bool | None = None
    allow_reserved: bool | None = None
    collection_format: str | None = None
    swagger: bool = False


class InputNodeSnapshot(BaseModel):
    """One frozen configurable input node."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    input_node_id: str
    node_kind: str
    canonical_path: str
    parent_node_id: str | None = None
    required: bool
    schema_contract: SchemaSnapshot | None = None


class OperationTestSnapshot(BaseModel):
    """Persistent request-generation model independent from later OpenAPI IRs."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    operation_key: str
    method: str
    path: str
    parameters: list[ParameterSnapshot]
    request_body_node_id: str | None = None
    media_type_node_ids: dict[str, str] = Field(default_factory=dict)
    media_type_encodings: dict[str, dict[str, Any]] = Field(default_factory=dict)
    available_media_types: list[str] = Field(default_factory=list)
    unsupported_parameter_nodes: dict[str, str] = Field(default_factory=dict)
    unsupported_schema_nodes: dict[str, list[str]] = Field(default_factory=dict)
    input_nodes: list[InputNodeSnapshot]


class GeneratorDisabledReason(BaseModel):
    """
    Coordinate generator disabled reason behavior for deterministic request generation,
    constraint solving, and execution.

    Read the public methods as the supported lifecycle and treat underscore-prefixed
    helpers as internal implementation details.
    """
    model_config = ConfigDict(frozen=True, extra="forbid")

    code: str
    message: str
    recoverable: bool = False
    input_node_id: str | None = None


class OperationGeneratorConfig(BaseModel):
    """Versioned configuration for all active inputs of one operation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    operation_key: str
    revision: int = Field(ge=1)
    snapshot: OperationTestSnapshot
    enabled: bool = True
    disabled_reasons: list[GeneratorDisabledReason] = Field(default_factory=list)
    active_media_type: str | None = None
    configs: list[InputGeneratorConfig]


GeneratorRevisionLifecycle = Literal[
    "candidate",
    "accepted",
    "rejected",
    "rollback",
]


class GeneratorConfigRevision(BaseModel):
    """One durable generator configuration revision and its evaluation state."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    operation_key: str
    revision: int = Field(ge=1)
    parent_revision: int | None = Field(default=None, ge=1)
    lifecycle: GeneratorRevisionLifecycle
    rollback_of_revision: int | None = Field(default=None, ge=1)
    restored_from_revision: int | None = Field(default=None, ge=1)
    hypothesis: dict[str, Any] | None = None
    config: OperationGeneratorConfig
    evaluation: dict[str, Any] | None = None
    created_at: datetime
    evaluated_at: datetime | None = None


class GeneratedNodeValue(BaseModel):
    """One concrete scalar generated for an input-node occurrence."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    input_node_id: str
    instance_path: str
    value: Any


class GeneratedTestCase(BaseModel):
    """Structured request inputs before OpenAPI parameter serialization."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    operation_key: str
    case_index: int = Field(ge=0)
    media_type: str | None = None
    path_parameters: dict[str, Any]
    query_parameters: dict[str, Any]
    header_parameters: dict[str, Any]
    cookie_parameters: dict[str, Any]
    body: Any | None = None
    body_present: bool = False
    generated_values: list[GeneratedNodeValue]
    omitted_input_node_ids: list[str]


class PreparedTestRequest(BaseModel):
    """A target-relative HTTP request ready for the shared transport."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    method: str
    path: str
    query_items: list[tuple[str, str]]
    query_allow_reserved_indices: list[int] = Field(default_factory=list)
    headers: dict[str, str]
    content: bytes | None = None


class PreparedRequestSummary(BaseModel):
    """Request evidence retained in an execution report."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    method: str
    path: str
    query_items: list[tuple[str, str]]
    headers: dict[str, str]
    body_size_bytes: int


class ResponseSummary(BaseModel):
    """Response metadata captured without consuming the response body."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    status_code: int
    reason_phrase: str
    media_type: str | None = None
    content_length: int | None = None
    latency_ms: float = Field(ge=0)


class TransportErrorSummary(BaseModel):
    """
    Carry validated transport error summary data across deterministic request
    generation, constraint solving, and execution.

    The annotated fields form the contract; validation rejects missing, extra, or
    incorrectly typed values at the boundary.
    """
    model_config = ConfigDict(frozen=True, extra="forbid")

    code: str
    message: str


class BehaviorMonitorWarningSummary(BaseModel):
    """
    Carry validated behavior monitor warning summary data across deterministic request
    generation, constraint solving, and execution.

    The annotated fields form the contract; validation rejects missing, extra, or
    incorrectly typed values at the boundary.
    """
    model_config = ConfigDict(frozen=True, extra="forbid")

    code: str
    message: str
    issues: list[str] = Field(default_factory=list)


class UniqueFailureMessage(BaseModel):
    """One normalized failure message and the cases that produced it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    failure_id: str
    message: str
    case_ids: list[str] = Field(default_factory=list)


class BatchFailureReport(BaseModel):
    """Deterministic failure messages for one bounded execution batch."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    unique_failure_messages: list[UniqueFailureMessage] = Field(
        default_factory=list
    )
    truncated: bool = False


class TestCaseExecutionReport(BaseModel):
    """
    Coordinate test case execution report behavior for deterministic request generation,
    constraint solving, and execution.

    Read the public methods as the supported lifecycle and treat underscore-prefixed
    helpers as internal implementation details.
    """
    model_config = ConfigDict(frozen=True, extra="forbid")

    case_id: str
    generated_test_case: GeneratedTestCase
    request: PreparedRequestSummary
    response: ResponseSummary | None = None
    transport_error: TransportErrorSummary | None = None
    behavior_monitor_warnings: list[BehaviorMonitorWarningSummary] = Field(
        default_factory=list
    )
    response_validation: Literal[
        "evaluated",
        "partial",
        "not_evaluated",
    ] = "not_evaluated"


class OperationExecutionReport(BaseModel):
    """Batch execution evidence including API behavior validation state."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: str
    operation_key: str
    seed: int
    config_revision: int
    status: Literal["completed", "partial", "errored"]
    response_validation: Literal[
        "evaluated",
        "partial",
        "not_evaluated",
    ] = "not_evaluated"
    cases: list[TestCaseExecutionReport]
    status_code_counts: dict[str, int]
    error_count: int
    observed_2xx: bool
    behavior_monitor_warning_count: int = 0
    failure_report: BatchFailureReport = Field(default_factory=BatchFailureReport)
