"""Traverse OpenAPI request and response Schemas with stable semantic paths.

The behavior-specific query modules share this private implementation for media
selection, response fallback, Schema recursion, handle normalization, and safe
lookup diagnostics. No model Tool is bound directly to these helpers.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from difflib import SequenceMatcher

from restscope.openapi_parser.ir import (
    MediaTypeIR,
    OperationIR,
    ResponseIR,
    SchemaIR,
)
from restscope.operation_references import RequestInputReference, ResponseFieldReference
from restscope.target_api.media_type import is_json_media_type, normalize_media_type
from restscope.tools.runtime import ToolFailure

_DEFAULT_LIST_LIMIT = 100
_MAX_ERROR_CHOICES = 10
_SIMILARITY_THRESHOLD = 0.95
_NAME_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_NAME_TOKEN = re.compile(r"[A-Za-z0-9]+")


@dataclass(frozen=True, slots=True)
class _SchemaEntry:
    """Keep one internal semantic handle attached to its resolved Schema node."""

    name: str
    location: str
    required: bool
    schema: SchemaIR | None
    media_type: str | None = None
    reference: RequestInputReference | ResponseFieldReference | None = None


@dataclass(frozen=True, slots=True)
class _ObservedFieldMatch:
    """Keep one globally unique observed field attached to its match score."""

    operation_key: str
    status_code: int
    matched_status_code: str
    media_type: str
    field: str
    similarity_score: float
    match_basis: str


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

    OpenAPI traversal remains this Tool Backend's responsibility because
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
    reference: RequestInputReference | ResponseFieldReference | None = None,
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
        if is_json_media_type(name)
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
    try:
        return ResponseFieldReference.from_handle(field).handle
    except ValueError as exc:
        raise ToolFailure(
            code="openapi_response_field_invalid",
            message=str(exc),
        ) from exc


def _observed_media_schema(
    contents: Mapping[str, MediaTypeIR],
    *,
    observed_media_type: str,
) -> tuple[str, SchemaIR] | None:
    """Map one normalized observation media type to one current Schema."""
    normalized = normalize_media_type(observed_media_type)
    candidates = [
        (name, media.schema)
        for name, media in contents.items()
        if media.schema is not None
        and normalize_media_type(name) == normalized
    ]
    if len(candidates) != 1:
        return None
    name, schema = candidates[0]
    assert schema is not None
    return name, schema


def _scalar_schema(schema: SchemaIR | None) -> bool:
    """Return whether an observed selector ends at one scalar Schema node."""
    if schema is None:
        return False
    if schema.properties or schema.items is not None:
        return False
    if schema.all_of or schema.any_of or schema.one_of:
        return False
    schema_types = (
        {schema.type}
        if isinstance(schema.type, str)
        else set(schema.type or [])
    )
    return bool(schema_types & {"string", "integer", "number", "boolean"})


def _field_similarity(
    requested: str,
    reference: ResponseFieldReference,
) -> tuple[float, str] | None:
    """Return one high-precision deterministic match or no candidate."""
    property_names = reference.property_names
    if not property_names:
        return None
    leaf = _normalized_name(property_names[-1])
    path = "".join(_normalized_name(item) for item in property_names)
    if requested == leaf:
        return (1.0, "normalized_exact")
    if requested == path:
        return (1.0, "path_exact")
    score = max(
        SequenceMatcher(None, requested, leaf).ratio(),
        SequenceMatcher(None, requested, path).ratio(),
    )
    if score < _SIMILARITY_THRESHOLD:
        return None
    return (round(score, 6), "high_similarity")


def _normalized_name(value: str) -> str:
    """Normalize camel, snake, kebab, and dotted names for exact comparison."""
    separated = _NAME_BOUNDARY.sub(" ", value)
    return "".join(_NAME_TOKEN.findall(separated)).casefold()


def _closest_operation_keys(
    requested: str,
    operations: Mapping[str, OperationIR],
) -> list[str]:
    """Rank real operation keys against one model-provided spelling.

    Models sometimes confuse RESTScope's ``METHOD /path`` key with an OpenAPI
    ``operationId`` or a human summary. Those alternate spellings help rank
    recovery choices, but only exact real keys are returned so this lookup
    never silently creates an alias.
    """
    normalized = _normalized_name(requested)
    ranked: list[tuple[float, str]] = []
    for operation in operations.values():
        spellings = (
            operation.operation_key,
            operation.operation_id or "",
            operation.summary or "",
        )
        score = max(
            (
                SequenceMatcher(
                    None,
                    normalized,
                    _normalized_name(spelling),
                ).ratio()
                for spelling in spellings
                if spelling
            ),
            default=0.0,
        )
        ranked.append((score, operation.operation_key))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    return [key for _score, key in ranked[:_MAX_ERROR_CHOICES]]


def _choice_text(values: Mapping[str, object]) -> str:
    """Render a bounded deterministic choice list for model-safe failures."""
    names = sorted(str(name) for name in values)
    retained = names[:_MAX_ERROR_CHOICES]
    suffix = f" (+{len(names) - len(retained)} more)" if len(names) > len(retained) else ""
    return ", ".join(retained) + suffix
