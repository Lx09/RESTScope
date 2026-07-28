"""Intermediate Representation (IR) data models for the OpenAPI parser."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


def _format_named_schema_item(name: str, schema: "SchemaIR | None", *, required: bool | None = None) -> str:
    """Format one named schema line for prompt-friendly contract text."""
    suffix = ""
    if required is True:
        suffix = " (required)"
    elif required is False:
        suffix = " (optional)"
    schema_text = schema.to_compact_text() if schema is not None else "type: unknown"
    return f"  - {name}{suffix}: {schema_text}"


def _iter_schema_path_items(
    schema: "SchemaIR | None",
    *,
    parent_path: str = "",
    required: bool | None = None,
    visited: set[int] | None = None,
) -> list[str]:
    """Flatten one schema into itemized path lines for prompts."""
    if schema is None:
        if parent_path:
            return [_format_named_schema_item(parent_path, None, required=required)]
        return []

    if visited is None:
        visited = set()

    schema_id = id(schema)
    if schema_id in visited:
        if parent_path:
            return [_format_named_schema_item(parent_path, schema, required=required)]
        return [f"  - {schema.to_compact_text()}"]

    visited = set(visited)
    visited.add(schema_id)

    if schema.type == "object" and schema.properties:
        lines: list[str] = []
        for prop_name, prop_schema in schema.properties.items():
            child_path = f"{parent_path}.{prop_name}" if parent_path else prop_name
            lines.extend(
                _iter_schema_path_items(
                    prop_schema,
                    parent_path=child_path,
                    required=prop_name in (schema.required or []),
                    visited=visited,
                )
            )
        return lines

    if schema.type == "array" and schema.items is not None:
        item_path = f"{parent_path}[]" if parent_path else "[]"
        if schema.items.type in {"object", "array"}:
            return _iter_schema_path_items(
                schema.items,
                parent_path=item_path,
                required=required,
                visited=visited,
            )
        return [_format_named_schema_item(item_path, schema.items, required=required)]

    if parent_path:
        return [_format_named_schema_item(parent_path, schema, required=required)]
    return [f"  - {schema.to_compact_text()}"]


def _iter_schema_path_items_json(
    schema: "SchemaIR | None",
    *,
    parent_path: str = "",
    required: bool | None = None,
    visited: set[int] | None = None,
) -> list[dict[str, Any]]:
    """Flatten one schema into list of dicts with field_path, schema_text, required, description."""
    if schema is None:
        return []

    if visited is None:
        visited = set()

    schema_id = id(schema)
    if schema_id in visited:
        return []

    visited = set(visited)
    visited.add(schema_id)

    path_items: list[dict[str, Any]] = []

    if schema.type == "object" and schema.properties:
        for prop_name, prop_schema in schema.properties.items():
            child_path = f"{parent_path}.{prop_name}" if parent_path else prop_name
            child_required = prop_name in (schema.required or [])
            path_items.extend(
                _iter_schema_path_items_json(
                    prop_schema,
                    parent_path=child_path,
                    required=child_required,
                    visited=visited,
                )
            )
        return path_items

    if schema.type == "array" and schema.items is not None:
        item_path = f"{parent_path}[]" if parent_path else "[]"
        if schema.items.type in {"object", "array"}:
            return _iter_schema_path_items_json(
                schema.items,
                parent_path=item_path,
                required=required,
                visited=visited,
            )
        return [{
            "field_path": item_path,
            "schema_text": schema.items.to_compact_text(),
            "required": required or False,
            "description": schema.items.description,
        }]

    if parent_path:
        return [{
            "field_path": parent_path,
            "schema_text": schema.to_compact_text(),
            "required": required or False,
            "description": schema.description,
        }]

    return []


def _format_request_body_lines(body: "RequestBodyIR") -> list[str]:
    """Format request-body schema lines, skipping empty sections."""
    media = next((item for item in body.contents.values() if item.schema is not None), None)
    if media is None or media.schema is None:
        return []

    lines = [f"- Request body ({'required' if body.required else 'optional'}):"]
    lines.extend(_iter_schema_path_items(media.schema))
    return lines


def _format_response_schema_lines(schema: "SchemaIR") -> list[str]:
    """Format one response schema into itemized lines."""
    return _iter_schema_path_items(schema)


@dataclass(slots=True)
class ParseInput:
    """Input for parsing."""
    raw_document: dict[str, Any]
    source_location: str | None
    source_kind: str


@dataclass(slots=True)
class ServerVariableIR:
    """Server variable definition."""
    name: str
    default: str | None
    enum: list[str]
    description: str | None


@dataclass(slots=True)
class ServerIR:
    """Server definition."""
    url: str
    description: str | None
    variables: dict[str, ServerVariableIR]


@dataclass(slots=True)
class SpecMetaIR:
    """Specification metadata."""
    spec_format: str
    spec_version: str
    title: str | None
    version: str | None
    description: str | None
    summary: str | None
    terms_of_service: str | None
    contact: dict | None
    license: dict | None
    external_docs: dict | None
    base_path: str | None
    servers: list["ServerIR"]


@dataclass(slots=True)
class SchemaIR:
    """Schema definition."""
    type: str | list[str] | None
    format: str | None
    title: str | None
    description: str | None

    properties: dict[str, "SchemaIR"]
    required: list[str]
    items: "SchemaIR | None"

    enum: list[object] | None
    const: object | None
    default: object | None
    nullable: bool | None
    read_only: bool | None
    write_only: bool | None
    deprecated: bool | None

    minimum: int | float | None
    maximum: int | float | None
    exclusive_minimum: int | float | bool | None
    exclusive_maximum: int | float | bool | None
    min_length: int | None
    max_length: int | None
    pattern: str | None
    min_items: int | None
    max_items: int | None
    unique_items: bool | None
    min_properties: int | None
    max_properties: int | None

    all_of: list["SchemaIR"]
    any_of: list["SchemaIR"]
    one_of: list["SchemaIR"]
    not_schema: "SchemaIR | None"

    additional_properties: "bool | SchemaIR | None"

    example: object | None
    examples: list[object]
    discriminator: dict | None
    xml: dict | None
    external_docs: dict | None

    source_pointer: str | None
    raw: dict[str, object]
    ref_path: str | None = None  # Original $ref path for circular reference handling

    def to_compact_text(self) -> str:
        """Convert schema to compact text, skipping empty properties."""
        parts = []
        if self.type:
            parts.append(f"type: {self.type}")
        if self.format:
            parts.append(f"format: {self.format}")
        if self.enum:
            parts.append(f"enum: {list(self.enum)[:5]}")
        if self.minimum is not None:
            parts.append(f"minimum: {self.minimum}")
        if self.maximum is not None:
            parts.append(f"maximum: {self.maximum}")
        if self.min_length is not None:
            parts.append(f"min_length: {self.min_length}")
        if self.max_length is not None:
            parts.append(f"max_length: {self.max_length}")
        if self.pattern:
            parts.append(f"pattern: {self.pattern}")
        return ", ".join(parts) if parts else "type: unknown"


@dataclass(slots=True)
class ExampleIR:
    """Example definition."""
    name: str
    summary: str | None
    description: str | None
    value: object | None
    external_value: str | None
    raw: dict[str, object]


@dataclass(slots=True)
class MediaTypeIR:
    """Media type definition."""
    media_type: str
    schema: "SchemaIR | None"
    example: object | None
    examples: dict[str, "ExampleIR"]
    encoding: dict[str, object]
    source_pointer: str | None
    raw: dict[str, object]


@dataclass(slots=True)
class HeaderIR:
    """Header definition."""
    name: str
    description: str | None
    required: bool
    deprecated: bool
    allow_empty_value: bool
    style: str | None
    explode: bool | None
    schema: "SchemaIR | None"
    content: dict[str, "MediaTypeIR"]
    raw: dict[str, object]


@dataclass(slots=True)
class LinkIR:
    """Link definition."""
    name: str
    operation_ref: str | None
    operation_id: str | None
    parameters: dict[str, object]
    request_body: object | None
    description: str | None
    server: "ServerIR | None"
    raw: dict[str, object]


@dataclass(slots=True)
class ResponseIR:
    """Response definition."""
    status_code: str
    description: str | None
    headers: dict[str, HeaderIR]
    contents: dict[str, MediaTypeIR]
    links: dict[str, LinkIR]
    source_pointer: str | None
    raw: dict[str, object]


@dataclass(slots=True)
class ResponsesIR:
    """Responses container."""
    by_status: dict[str, ResponseIR]


@dataclass(slots=True)
class SecuritySchemeIR:
    """Security scheme definition."""
    name: str
    type: str
    location: str | None
    api_key_name: str | None
    scheme: str | None
    bearer_format: str | None
    flows: dict[str, object]
    open_id_connect_url: str | None
    description: str | None
    raw: dict[str, object]


@dataclass(slots=True)
class SecurityRequirementIR:
    """Security requirement definition."""
    scheme_name: str
    scopes: list[str]


@dataclass(slots=True)
class OperationSecurityIR:
    """Operation security definition."""
    requirements: list[SecurityRequirementIR]
    requirement_sets: list[list[SecurityRequirementIR]]
    resolved_schemes: dict[str, SecuritySchemeIR]


@dataclass(slots=True)
class ParameterIR:
    """Parameter definition."""
    name: str
    location: str
    required: bool
    deprecated: bool
    allow_empty_value: bool
    style: str | None
    explode: bool | None
    allow_reserved: bool | None

    description: str | None
    example: object | None
    examples: dict[str, "ExampleIR"]
    content: dict[str, "MediaTypeIR"]

    schema: "SchemaIR | None"

    synthetic: bool
    source_pointer: str | None
    raw: dict[str, object]


InputNodeKind = Literal[
    "parameter",
    "request_body",
    "media_type",
    "object",
    "array",
    "scalar",
    "variant",
]


@dataclass(slots=True, frozen=True)
class InputNodeIR:
    """Stable, operation-scoped identity for one configurable request input."""

    input_node_id: str
    node_kind: InputNodeKind
    canonical_path: str
    parent_node_id: str | None
    schema: "SchemaIR | None"


@dataclass(slots=True)
class RequestBodyIR:
    """Request body definition."""
    required: bool
    description: str | None
    contents: dict[str, "MediaTypeIR"]
    source_pointer: str | None
    raw: dict[str, object]


@dataclass(slots=True)
class ComponentsIR:
    """Components container."""
    schemas: dict[str, "SchemaIR"]
    parameters: dict[str, "ParameterIR"]
    request_bodies: dict[str, "RequestBodyIR"]
    responses: dict[str, "ResponseIR"]
    headers: dict[str, "HeaderIR"]
    security_schemes: dict[str, "SecuritySchemeIR"]
    examples: dict[str, "ExampleIR"]
    links: dict[str, "LinkIR"]
    callbacks: dict[str, object]
    path_items: dict[str, object]


@dataclass(slots=True)
class PathItemIR:
    """Path item definition."""
    path: str
    summary: str | None
    description: str | None
    shared_parameters: list["ParameterIR"]
    operations: dict[str, str]  # method -> operation_key
    extensions: dict[str, object]


@dataclass(slots=True)
class OperationIR:
    """Operation definition."""
    operation_key: str
    operation_id: str | None
    path: str
    method: str
    tags: list[str]
    summary: str | None
    description: str | None
    deprecated: bool

    path_parameters: list["ParameterIR"]
    query_parameters: list["ParameterIR"]
    header_parameters: list["ParameterIR"]
    cookie_parameters: list["ParameterIR"]

    request_body: "RequestBodyIR | None"
    responses: "ResponsesIR"
    security: "OperationSecurityIR"
    servers: list["ServerIR"]

    callbacks: dict[str, object]
    links: dict[str, object]
    extensions: dict[str, object]

    diagnostics: list["DiagnosticItemIR"]
    input_nodes: dict[str, InputNodeIR] = field(default_factory=dict)

    def to_request_schema_text(self) -> str:
        """Render request contract as compact itemized text for prompts."""
        lines: list[str] = []
        sections = (
            ("Path parameters", self.path_parameters),
            ("Query parameters", self.query_parameters),
            ("Headers", self.header_parameters),
            ("Cookies", self.cookie_parameters),
        )
        for title, params in sections:
            if not params:
                continue
            lines.append(f"- {title}:")
            for param in params:
                lines.append(
                    _format_named_schema_item(
                        param.name,
                        param.schema,
                        required=param.required,
                    )
                )

        if self.request_body is not None:
            lines.extend(_format_request_body_lines(self.request_body))

        return "\n".join(lines) if lines else "- No request parameters or body"

    def to_request_schema_json(self) -> list[dict[str, Any]]:
        """Return JSON-serializable list of all request parameters with expanded body fields.

        Each dict contains:
        - param_location: "path" | "query" | "header" | "cookie" | "body"
        - param_name: parameter name (or "request_body" for body fields)
        - field_path: "" for non-body, property path for body fields
        - schema_text: compact schema description
        - required: bool
        - description: str | None

        Body fields are expanded recursively with descriptions from SchemaIR.
        """
        items: list[dict[str, Any]] = []

        # Path/Query/Header/Cookie parameters
        for location, param_list in [
            ("path", self.path_parameters),
            ("query", self.query_parameters),
            ("header", self.header_parameters),
            ("cookie", self.cookie_parameters),
        ]:
            for param in param_list:
                items.append({
                    "param_location": location,
                    "param_name": param.name,
                    "field_path": "",
                    "schema_text": param.schema.to_compact_text() if param.schema else "type: unknown",
                    "required": param.required,
                    "description": param.description,
                })

        # Request body - expand all properties
        if self.request_body:
            for media_type, media in self.request_body.contents.items():
                if "json" in media_type.lower() and media.schema:
                    body_items = _iter_schema_path_items_json(media.schema)
                    for bi in body_items:
                        items.append({
                            "param_location": "body",
                            "param_name": "request_body",
                            "field_path": bi["field_path"],
                            "schema_text": bi["schema_text"],
                            "required": bi["required"],
                            "description": bi["description"],
                        })
                    break  # Only first JSON media type

        return items

    def to_response_schema_text(self, status_code: str) -> str:
        """Render one response contract as compact itemized text for prompts."""
        response = self.responses.by_status.get(status_code)
        if response is None:
            return f"- No response schema defined for status {status_code}"
        if not response.contents:
            return f"- No content for status {status_code}"

        media = next((item for item in response.contents.values() if item.schema is not None), None)
        if media is None or media.schema is None:
            return f"- No response schema defined for status {status_code}"

        lines = [f"- Response schema for status {status_code}:"]
        lines.extend(_format_response_schema_lines(media.schema))
        return "\n".join(lines)


@dataclass(slots=True)
class DiagnosticItemIR:
    """Diagnostic item definition."""
    severity: str
    code: str
    message: str
    path: str | None
    method: str | None
    pointer: str | None
    exception_type: str | None
    extras: dict[str, object]


@dataclass(slots=True)
class DiagnosticsIR:
    """Diagnostics container."""
    spec_errors: list[DiagnosticItemIR]
    spec_warnings: list[DiagnosticItemIR]
    path_errors: list[DiagnosticItemIR]
    operation_errors: list[DiagnosticItemIR]


@dataclass(slots=True)
class SpecIndexesIR:
    """Operation lookup indexes for a parsed specification."""
    by_operation_id: dict[str, str]
    by_method_path: dict[tuple[str, str], str]


@dataclass(slots=True)
class OpenAPISpecIR:
    """Final intermediate representation of the OpenAPI spec."""
    meta: SpecMetaIR
    components: ComponentsIR
    paths: dict[str, PathItemIR]
    operations: dict[str, OperationIR]
    indexes: SpecIndexesIR
    diagnostics: DiagnosticsIR
