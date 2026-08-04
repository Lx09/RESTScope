"""Expose compact, read-only queries over the App's current OpenAPI IR.

The App parses one OpenAPI document into an in-memory representation (IR) and
stores it in ``ToolContext``.  ``OpenAPICapability`` reads that trusted IR only
when a registered Agent tool executes.  Models provide an exact operation key
and a narrow query; they never receive the IR itself.

The four public tool specifications cover listing request inputs, listing
response-body fields, inspecting one input Schema, and inspecting one response
field Schema. Their shared implementation owns handle construction, media-type
selection, response-status fallback, array-path normalization, pagination, and
safe failures.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from restscope.llm import ToolSpec
from restscope.openapi_parser.ir import (
    MediaTypeIR,
    OpenAPISpecIR,
    OperationIR,
    ResponseIR,
    SchemaIR,
)
from restscope.request_inputs import RequestInputReference

from .agent_tools import ToolFailure
from .tool_context import ToolContext


OPENAPI_LIST_INPUTS_TOOL_NAME = "openapi.list_inputs"
OPENAPI_LIST_RESPONSE_FIELDS_TOOL_NAME = "openapi.list_response_fields"
OPENAPI_GET_INPUT_SCHEMA_TOOL_NAME = "openapi.get_input_schema"
OPENAPI_GET_RESPONSE_FIELD_SCHEMA_TOOL_NAME = (
    "openapi.get_response_field_schema"
)

_DEFAULT_LIST_LIMIT = 100
_MAX_LIST_LIMIT = 200
_MAX_ERROR_CHOICES = 10
_MAX_ENUM_VALUES = 50
_MAX_SCHEMA_TEXT_CHARS = 800
_ARRAY_INDEX = re.compile(r"\[(\d*)\]")
_COMBINERS = {"allOf", "anyOf", "oneOf"}


def openapi_list_inputs_tool_spec() -> ToolSpec:
    """Describe the paginated request-input discovery tool.

    The result deliberately contains handles rather than Schemas.  An Agent
    that needs one contract can call ``openapi.get_input_schema`` afterward,
    which avoids paying prompt tokens for unrelated inputs.
    """
    return ToolSpec(
        name=OPENAPI_LIST_INPUTS_TOOL_NAME,
        description=(
            "List semantic request-input handles for an exact OpenAPI "
            "operation. Results are paginated and do not include Schemas."
        ),
        kind="local_function",
        input_schema={
            "type": "object",
            "properties": {
                "operation_key": {"type": "string", "minLength": 1},
                "media_type": {"type": "string", "minLength": 1},
                "prefix": {"type": "string", "minLength": 1},
                "offset": {
                    "type": "integer",
                    "minimum": 0,
                    "default": 0,
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": _MAX_LIST_LIMIT,
                    "default": _DEFAULT_LIST_LIMIT,
                },
            },
            "required": ["operation_key"],
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "properties": {
                "operation_key": {"type": "string"},
                "inputs": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "media_type": {"type": "string"},
                        },
                        "required": ["name"],
                        "additionalProperties": False,
                    },
                },
                "total": {"type": "integer", "minimum": 0},
                "offset": {"type": "integer", "minimum": 0},
                "next_offset": {"type": "integer", "minimum": 0},
            },
            "required": ["operation_key", "inputs", "total", "offset"],
            "additionalProperties": False,
        },
    )


def openapi_list_response_fields_tool_spec() -> ToolSpec:
    """Describe the paginated response-field discovery tool.

    The result contains semantic field handles rather than Schemas. A caller
    can pass one returned handle to ``openapi.get_response_field_schema`` when
    it needs the exact contract for that field.
    """
    return ToolSpec(
        name=OPENAPI_LIST_RESPONSE_FIELDS_TOOL_NAME,
        description=(
            "List semantic response-body field handles for one status of an "
            "exact OpenAPI operation. Results are paginated and do not include "
            "Schemas."
        ),
        kind="local_function",
        input_schema={
            "type": "object",
            "properties": {
                "operation_key": {"type": "string", "minLength": 1},
                "status_code": {
                    "oneOf": [
                        {
                            "type": "integer",
                            "minimum": 100,
                            "maximum": 599,
                        },
                        {
                            "type": "string",
                            "pattern": (
                                "^(?:[1-5][0-9]{2}|[1-5][xX]{2}|"
                                "[dD][eE][fF][aA][uU][lL][tT])$"
                            ),
                        },
                    ]
                },
                "offset": {
                    "type": "integer",
                    "minimum": 0,
                    "default": 0,
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": _MAX_LIST_LIMIT,
                    "default": _DEFAULT_LIST_LIMIT,
                },
            },
            "required": ["operation_key", "status_code"],
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "properties": {
                "operation_key": {"type": "string"},
                "requested_status_code": {"type": "string"},
                "matched_status_code": {"type": "string"},
                "media_type": {"type": "string"},
                "fields": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {"name": {"type": "string"}},
                        "required": ["name"],
                        "additionalProperties": False,
                    },
                },
                "total": {"type": "integer", "minimum": 0},
                "offset": {"type": "integer", "minimum": 0},
                "next_offset": {"type": "integer", "minimum": 0},
            },
            "required": [
                "operation_key",
                "requested_status_code",
                "matched_status_code",
                "media_type",
                "fields",
                "total",
                "offset",
            ],
            "additionalProperties": False,
        },
    )


def openapi_get_input_schema_tool_spec() -> ToolSpec:
    """Describe the exact request-input Schema lookup tool."""
    return ToolSpec(
        name=OPENAPI_GET_INPUT_SCHEMA_TOOL_NAME,
        description=(
            "Return the compact Schema for one exact semantic request input. "
            "Body inputs may need an explicit media_type."
        ),
        kind="local_function",
        input_schema={
            "type": "object",
            "properties": {
                "operation_key": {"type": "string", "minLength": 1},
                "input": {"type": "string", "minLength": 1},
                "media_type": {"type": "string", "minLength": 1},
            },
            "required": ["operation_key", "input"],
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "properties": {
                "operation_key": {"type": "string"},
                "input": {"type": "string"},
                "location": {
                    "type": "string",
                    "enum": ["path", "query", "header", "cookie", "body"],
                },
                "required": {"type": "boolean"},
                "media_type": {"type": "string"},
                "schema": {"type": "object"},
            },
            "required": [
                "operation_key",
                "input",
                "location",
                "required",
                "schema",
            ],
            "additionalProperties": False,
        },
    )


def openapi_get_response_field_schema_tool_spec() -> ToolSpec:
    """Describe the exact response-body field Schema lookup tool."""
    return ToolSpec(
        name=OPENAPI_GET_RESPONSE_FIELD_SCHEMA_TOOL_NAME,
        description=(
            "Return the compact Schema for one response-body field in an exact "
            "OpenAPI operation. Concrete array indexes are accepted."
        ),
        kind="local_function",
        input_schema={
            "type": "object",
            "properties": {
                "operation_key": {"type": "string", "minLength": 1},
                "status_code": {
                    "oneOf": [
                        {
                            "type": "integer",
                            "minimum": 100,
                            "maximum": 599,
                        },
                        {
                            "type": "string",
                            "pattern": (
                                "^(?:[1-5][0-9]{2}|[1-5][xX]{2}|"
                                "[dD][eE][fF][aA][uU][lL][tT])$"
                            ),
                        },
                    ]
                },
                "field": {
                    "type": "string",
                    "pattern": "^body(?:$|\\.|\\[)",
                },
                "media_type": {"type": "string", "minLength": 1},
            },
            "required": ["operation_key", "status_code", "field"],
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "properties": {
                "operation_key": {"type": "string"},
                "requested_status_code": {"type": "string"},
                "matched_status_code": {"type": "string"},
                "field": {"type": "string"},
                "media_type": {"type": "string"},
                "required": {"type": "boolean"},
                "schema": {"type": "object"},
            },
            "required": [
                "operation_key",
                "requested_status_code",
                "matched_status_code",
                "field",
                "media_type",
                "required",
                "schema",
            ],
            "additionalProperties": False,
        },
    )


@dataclass(frozen=True, slots=True)
class _SchemaEntry:
    """Keep one internal semantic handle attached to its resolved Schema node."""

    name: str
    location: str
    required: bool
    schema: SchemaIR | None
    media_type: str | None = None
    reference: RequestInputReference | None = None


class OpenAPICapability:
    """Answer four compact queries against the App's current OpenAPI IR.

    Args:
        context_provider: Trusted runtime callback that returns the App's
            initialized ``ToolContext``.  The model never receives or selects
            this dependency.

    The methods return structured tool payloads without mutating the IR.  An
    Agent registers only the methods it needs in its own ``AgentToolbox``.
    Expected lookup problems become stable ``ToolFailure`` values; unexpected
    programming errors remain owned by the toolbox's protected error path.
    """

    def __init__(self, *, context_provider: Callable[[], ToolContext]) -> None:
        """Retain the trusted context callback without reading App state early."""
        self._context_provider = context_provider

    def list_inputs(
        self,
        *,
        operation_key: str,
        media_type: str | None = None,
        prefix: str | None = None,
        offset: int = 0,
        limit: int = _DEFAULT_LIST_LIMIT,
    ) -> dict[str, Any]:
        """Return one deterministic page of request handles.

        ``media_type`` filters only request-body entries because ordinary HTTP
        Parameters do not vary by request content type.  ``prefix`` uses exact
        handle text, so callers can narrow a large body without retrieving its
        Schemas.  The toolbox validates numeric limits before this method runs.
        """
        operation = self._operation(operation_key)
        entries = [
            entry
            for entry in _operation_inputs(operation)
            if (
                (entry.media_type is None or media_type is None or entry.media_type == media_type)
                and (prefix is None or entry.name.startswith(prefix))
            )
        ]
        entries.sort(key=lambda item: (item.name, item.media_type or ""))
        page = entries[offset : offset + limit]
        inputs = [
            {
                "name": entry.name,
                **(
                    {"media_type": entry.media_type}
                    if entry.media_type is not None
                    else {}
                ),
            }
            for entry in page
        ]
        result: dict[str, Any] = {
            "operation_key": operation.operation_key,
            "inputs": inputs,
            "total": len(entries),
            "offset": offset,
        }
        next_offset = offset + len(page)
        if next_offset < len(entries):
            result["next_offset"] = next_offset
        return {"structured": result}

    def list_response_fields(
        self,
        *,
        operation_key: str,
        status_code: int | str,
        offset: int = 0,
        limit: int = _DEFAULT_LIST_LIMIT,
    ) -> dict[str, Any]:
        """Return one deterministic page of response-body field handles.

        The operation and response status select one response contract. The
        selected response is expected to have one Schema-bearing media type;
        existing lookup failures report missing or ambiguous contracts. The
        toolbox validates pagination values before this method runs.
        """
        operation = self._operation(operation_key)
        requested_status = _normalize_status_code(status_code)
        matched_status, response = _select_response(
            operation,
            requested_status=requested_status,
        )
        selected_media_type, schema = _select_media_schema(
            response.contents,
            requested=None,
            subject=f"response {matched_status}",
        )
        entries = _schema_entries(
            schema,
            name="body",
            required=False,
            location="body",
            media_type=selected_media_type,
            skip_write_only=True,
        )
        entries.sort(key=lambda item: item.name)
        page = entries[offset : offset + limit]
        result: dict[str, Any] = {
            "operation_key": operation.operation_key,
            "requested_status_code": requested_status,
            "matched_status_code": matched_status,
            "media_type": selected_media_type,
            "fields": [{"name": entry.name} for entry in page],
            "total": len(entries),
            "offset": offset,
        }
        next_offset = offset + len(page)
        if next_offset < len(entries):
            result["next_offset"] = next_offset
        return {"structured": result}

    def get_input_schema(
        self,
        *,
        operation_key: str,
        input: str,
        media_type: str | None = None,
    ) -> dict[str, Any]:
        """Return the compact Schema for one exact request-input handle.

        Body handles are resolved inside one selected request media type.
        Supplying a media type for path, query, header, or cookie input is an
        actionable caller mistake rather than a silently ignored argument.
        """
        operation = self._operation(operation_key)
        if input == "body" or input.startswith("body.") or input.startswith("body["):
            selected_media_type, schema = _select_media_schema(
                operation.request_body.contents if operation.request_body else {},
                requested=media_type,
                subject="request body",
            )
            entries = _schema_entries(
                schema,
                name="body",
                required=bool(operation.request_body and operation.request_body.required),
                location="body",
                media_type=selected_media_type,
                skip_read_only=True,
                reference=RequestInputReference.body(),
            )
        else:
            if media_type is not None:
                raise ToolFailure(
                    code="openapi_input_media_type_not_allowed",
                    message="media_type is accepted only for body inputs.",
                )
            selected_media_type = None
            entries = _ordinary_input_entries(operation)

        entry = next((item for item in entries if item.name == input), None)
        if entry is None:
            raise ToolFailure(
                code="openapi_input_not_found",
                message=(
                    f"OpenAPI input was not found in {operation.operation_key}: {input}"
                ),
            )
        result = {
            "operation_key": operation.operation_key,
            "input": entry.name,
            "location": entry.location,
            "required": entry.required,
            **(
                {"media_type": selected_media_type}
                if selected_media_type is not None
                else {}
            ),
            "schema": _schema_summary(entry.schema),
        }
        return {"structured": result}

    def get_response_field_schema(
        self,
        *,
        operation_key: str,
        status_code: int | str,
        field: str,
        media_type: str | None = None,
    ) -> dict[str, Any]:
        """Return the compact Schema for one exact response-body field.

        Numeric array indexes from concrete Catalog evidence are normalized to
        the ``[]`` Schema handle.  OpenAPI combiner branch indexes such as
        ``anyOf[0]`` remain intact because they select a Schema branch rather
        than one runtime array element.
        """
        operation = self._operation(operation_key)
        requested_status = _normalize_status_code(status_code)
        matched_status, response = _select_response(
            operation,
            requested_status=requested_status,
        )
        selected_media_type, schema = _select_media_schema(
            response.contents,
            requested=media_type,
            subject=f"response {matched_status}",
        )
        normalized_field = _normalize_response_field(field)
        entries = _schema_entries(
            schema,
            name="body",
            required=False,
            location="body",
            media_type=selected_media_type,
            skip_write_only=True,
        )
        entry = next(
            (item for item in entries if item.name == normalized_field),
            None,
        )
        if entry is None:
            raise ToolFailure(
                code="openapi_response_field_not_found",
                message=(
                    "OpenAPI response field was not found in "
                    f"{operation.operation_key} {matched_status} "
                    f"{selected_media_type}: {normalized_field}"
                ),
            )
        return {
            "structured": {
                "operation_key": operation.operation_key,
                "requested_status_code": requested_status,
                "matched_status_code": matched_status,
                "field": entry.name,
                "media_type": selected_media_type,
                "required": entry.required,
                "schema": _schema_summary(entry.schema),
            }
        }

    def _operation(self, operation_key: str) -> OperationIR:
        """Resolve one exact operation from the latest trusted App context."""
        ir = self._current_ir()
        try:
            return ir.operations[operation_key]
        except KeyError as exc:
            raise ToolFailure(
                code="openapi_operation_not_found",
                message=f"OpenAPI operation was not found: {operation_key}",
            ) from exc

    def _current_ir(self) -> OpenAPISpecIR:
        """Read the current in-memory document without exposing it to callers."""
        return self._context_provider().ir


def operation_parameter_handles(operation: OperationIR) -> frozenset[str]:
    """Return all legal semantic request handles for deterministic Catalog use.

    The Catalog accepts handles from every declared request media type so a
    query for an inactive-but-valid Body field returns the explicit
    ``parameter_not_used_in_request`` status instead of being mistaken for a
    forged name. This direct runtime consumer shares the exact traversal used
    by the model-facing request lookup tools.
    """
    return frozenset(
        reference.handle
        for reference in operation_input_references(operation)
    )


def operation_input_references(
    operation: OperationIR,
) -> tuple[RequestInputReference, ...]:
    """Return every declared request input through the shared semantic Interface.

    OpenAPI traversal remains this Capability's Adapter responsibility because
    it also owns media-type and Schema selection.  Handle grammar and concrete
    request-JSON traversal belong to :class:`RequestInputReference`.
    """
    references = {
        entry.reference.handle: entry.reference
        for entry in _operation_inputs(operation)
        if entry.reference is not None
    }
    return tuple(references[handle] for handle in sorted(references))


def _operation_inputs(operation: OperationIR) -> list[_SchemaEntry]:
    """Collect ordinary and per-media request-body entries for one operation."""
    output = _ordinary_input_entries(operation)
    if operation.request_body is None:
        return output
    for media_type, media in sorted(operation.request_body.contents.items()):
        if media.schema is None:
            continue
        output.extend(
            _schema_entries(
                media.schema,
                name="body",
                required=operation.request_body.required,
                location="body",
                media_type=media_type,
                skip_read_only=True,
                reference=RequestInputReference.body(),
            )
        )
    return output


def _ordinary_input_entries(operation: OperationIR) -> list[_SchemaEntry]:
    """Flatten path, query, header, and cookie Parameters into handles."""
    output: list[_SchemaEntry] = []
    for location, parameters in (
        ("path", operation.path_parameters),
        ("query", operation.query_parameters),
        ("header", operation.header_parameters),
        ("cookie", operation.cookie_parameters),
    ):
        for parameter in parameters:
            # Header names are case-insensitive on the wire.  Lowercasing here
            # keeps OpenAPI lookup aligned with generation, Catalog, and Patch.
            name = parameter.name.lower() if location == "header" else parameter.name
            reference = RequestInputReference.parameter(location, name)
            output.extend(
                _schema_entries(
                    parameter.schema,
                    name=reference.handle,
                    required=parameter.required,
                    location=location,
                    skip_read_only=True,
                    reference=reference,
                )
            )
    return output


def _schema_entries(
    schema: SchemaIR | None,
    *,
    name: str,
    required: bool,
    location: str,
    media_type: str | None = None,
    skip_read_only: bool = False,
    skip_write_only: bool = False,
    visited: frozenset[int] = frozenset(),
    reference: RequestInputReference | None = None,
) -> list[_SchemaEntry]:
    """Flatten one resolved Schema into stable, model-facing semantic handles."""
    item = _SchemaEntry(
        name=name,
        location=location,
        required=required,
        schema=schema,
        media_type=media_type,
        reference=reference,
    )
    if schema is None or id(schema) in visited:
        return [item]

    next_visited = visited | {id(schema)}
    output = [item]
    for property_name, child in sorted(schema.properties.items()):
        if (skip_read_only and child.read_only) or (
            skip_write_only and child.write_only
        ):
            continue
        output.extend(
            _schema_entries(
                child,
                name=(
                    reference.property(property_name).handle
                    if reference is not None
                    else f"{name}.{property_name}"
                ),
                required=property_name in schema.required,
                location=location,
                media_type=media_type,
                skip_read_only=skip_read_only,
                skip_write_only=skip_write_only,
                visited=next_visited,
                reference=(
                    reference.property(property_name)
                    if reference is not None
                    else None
                ),
            )
        )
    if schema.items is not None:
        output.extend(
            _schema_entries(
                schema.items,
                name=(
                    reference.items().handle
                    if reference is not None
                    else f"{name}[]"
                ),
                required=required,
                location=location,
                media_type=media_type,
                skip_read_only=skip_read_only,
                skip_write_only=skip_write_only,
                visited=next_visited,
                reference=(
                    reference.items() if reference is not None else None
                ),
            )
        )
    for combiner, branches in (
        ("allOf", schema.all_of),
        ("anyOf", schema.any_of),
        ("oneOf", schema.one_of),
    ):
        for index, branch in enumerate(branches):
            output.extend(
                _schema_entries(
                    branch,
                    name=(
                        reference.variant(combiner, index).handle
                        if reference is not None
                        else f"{name}.{combiner}[{index}]"
                    ),
                    required=required,
                    location=location,
                    media_type=media_type,
                    skip_read_only=skip_read_only,
                    skip_write_only=skip_write_only,
                    visited=next_visited,
                    reference=(
                        reference.variant(combiner, index)
                        if reference is not None
                        else None
                    ),
                )
            )
    return output


def _select_media_schema(
    contents: Mapping[str, MediaTypeIR],
    *,
    requested: str | None,
    subject: str,
) -> tuple[str, SchemaIR]:
    """Select one Schema-bearing media type without silently merging contracts."""
    candidates = {
        name: media.schema
        for name, media in sorted(contents.items())
        if media.schema is not None
    }
    if requested is not None:
        selected = candidates.get(requested)
        if selected is None:
            raise ToolFailure(
                code="openapi_media_type_not_found",
                message=(
                    f"OpenAPI {subject} media type was not found: {requested}. "
                    f"Available: {_choice_text(candidates)}"
                ),
            )
        return requested, selected

    json_candidates = {
        name: schema
        for name, schema in candidates.items()
        if _is_json_media_type(name)
    }
    if len(json_candidates) == 1:
        return next(iter(json_candidates.items()))
    if len(candidates) == 1:
        return next(iter(candidates.items()))
    if not candidates:
        raise ToolFailure(
            code="openapi_schema_not_found",
            message=f"OpenAPI {subject} has no Schema-bearing media type.",
        )
    raise ToolFailure(
        code="openapi_media_type_ambiguous",
        message=(
            f"OpenAPI {subject} has multiple possible media types. "
            f"Choose one of: {_choice_text(candidates)}"
        ),
    )


def _select_response(
    operation: OperationIR,
    *,
    requested_status: str,
) -> tuple[str, ResponseIR]:
    """Apply exact, status-class, then default OpenAPI response matching."""
    responses = operation.responses.by_status
    by_casefold = {key.casefold(): (key, value) for key, value in responses.items()}
    exact = by_casefold.get(requested_status.casefold())
    if exact is not None:
        return exact
    if requested_status.isdigit():
        wildcard = by_casefold.get(f"{requested_status[0]}xx".casefold())
        if wildcard is not None:
            return wildcard
    default = by_casefold.get("default")
    if default is not None:
        return default
    raise ToolFailure(
        code="openapi_response_not_found",
        message=(
            f"OpenAPI response was not found for {operation.operation_key} "
            f"status {requested_status}. Available: {_choice_text(responses)}"
        ),
    )


def _normalize_status_code(status_code: int | str) -> str:
    """Normalize model-provided status text while preserving OpenAPI wildcards."""
    if isinstance(status_code, int):
        return str(status_code)
    if status_code.casefold() == "default":
        return "default"
    if status_code[1:].casefold() == "xx":
        return status_code[0] + "XX"
    return status_code


def _normalize_response_field(field: str) -> str:
    """Convert concrete runtime array indexes to semantic ``[]`` handles."""
    if field == "body":
        return field
    segments = field.split(".")
    if not segments or not segments[0].startswith("body"):
        raise ToolFailure(
            code="openapi_response_field_invalid",
            message="Response field must start with body.",
        )
    normalized: list[str] = []
    for segment in segments:
        name = segment.split("[", 1)[0]
        suffix = segment[len(name) :]
        if name in _COMBINERS:
            if not re.fullmatch(r"\[\d+\]", suffix):
                raise ToolFailure(
                    code="openapi_response_field_invalid",
                    message=f"Invalid OpenAPI combiner field segment: {segment}",
                )
            normalized.append(segment)
            continue
        if suffix and not re.fullmatch(r"(?:\[\d*\])+", suffix):
            raise ToolFailure(
                code="openapi_response_field_invalid",
                message=f"Invalid response field segment: {segment}",
            )
        normalized.append(name + _ARRAY_INDEX.sub("[]", suffix))
    return ".".join(normalized)


def _schema_summary(schema: SchemaIR | None) -> dict[str, Any]:
    """Return only one node's bounded structural and validation facts."""
    if schema is None:
        return {}
    enum_values = list(schema.enum) if schema.enum is not None else None
    values: dict[str, Any] = {
        "type": schema.type,
        "format": schema.format,
        "nullable": schema.nullable,
        "enum": (
            [_bound_schema_value(item) for item in enum_values[:_MAX_ENUM_VALUES]]
            if enum_values is not None
            else None
        ),
        "enum_count": len(enum_values) if enum_values is not None else None,
        "enum_truncated": (
            len(enum_values) > _MAX_ENUM_VALUES
            if enum_values is not None
            else None
        ),
        "const": _bound_schema_value(schema.const),
        "minimum": schema.minimum,
        "maximum": schema.maximum,
        "exclusive_minimum": schema.exclusive_minimum,
        "exclusive_maximum": schema.exclusive_maximum,
        "min_length": schema.min_length,
        "max_length": schema.max_length,
        "pattern": _bound_schema_value(schema.pattern),
        "min_items": schema.min_items,
        "max_items": schema.max_items,
        "unique_items": schema.unique_items,
        "min_properties": schema.min_properties,
        "max_properties": schema.max_properties,
        "additional_properties": _additional_properties_summary(
            schema.additional_properties
        ),
    }
    return {name: value for name, value in values.items() if value is not None}


def _additional_properties_summary(value: bool | SchemaIR | None) -> Any | None:
    """Describe whether an object accepts undeclared fields without a subtree."""
    if isinstance(value, SchemaIR):
        # The selected node owns only the shape of additional values. Returning
        # their full subtree would defeat exact-node lookup and could recurse
        # forever for a circular Schema.
        return {
            "schema": {
                name: item
                for name, item in {
                    "type": value.type,
                    "format": value.format,
                    "nullable": value.nullable,
                }.items()
                if item is not None
            }
        }
    return value


def _bound_schema_value(value: Any, *, depth: int = 0) -> Any:
    """Bound untrusted Schema literals so one exact query stays token-safe."""
    if isinstance(value, str):
        if len(value) <= _MAX_SCHEMA_TEXT_CHARS:
            return value
        return {
            "truncated": True,
            "original_chars": len(value),
            "value": value[:_MAX_SCHEMA_TEXT_CHARS],
        }
    if depth >= 3:
        return {"truncated": True, "type": type(value).__name__}
    if isinstance(value, list):
        retained = value[:_MAX_ENUM_VALUES]
        output = [_bound_schema_value(item, depth=depth + 1) for item in retained]
        if len(value) > len(retained):
            output.append({"truncated_items": len(value) - len(retained)})
        return output
    if isinstance(value, dict):
        items = list(value.items())[:_MAX_ENUM_VALUES]
        output = {
            str(name): _bound_schema_value(item, depth=depth + 1)
            for name, item in items
        }
        if len(value) > len(items):
            output["<truncated_properties>"] = len(value) - len(items)
        return output
    return value


def _choice_text(values: Mapping[str, Any]) -> str:
    """Render a bounded deterministic choice list for model-safe failures."""
    names = sorted(str(name) for name in values)
    retained = names[:_MAX_ERROR_CHOICES]
    suffix = f" (+{len(names) - len(retained)} more)" if len(names) > len(retained) else ""
    return ", ".join(retained) + suffix


def _is_json_media_type(media_type: str) -> bool:
    """Recognize ordinary and vendor-specific JSON media types."""
    normalized = media_type.split(";", 1)[0].strip().casefold()
    return normalized == "application/json" or normalized.endswith("+json")
