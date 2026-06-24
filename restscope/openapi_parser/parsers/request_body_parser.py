"""Request body parser module."""

from ..adapters.base import SpecificationAdapter
from ..diagnostics import make_diagnostic
from ..ir import DiagnosticsIR, ExampleIR, MediaTypeIR, RequestBodyIR, SchemaIR
from ..resolver import ReferenceResolver
from .schema_parser import parse_schema


def parse_media_type_for_parameter(
    media_raw: dict,
    resolver: ReferenceResolver | None,
    scope: str | None,
) -> MediaTypeIR:
    """
    Parse a media type object for parameter content.

    Args:
        media_raw: The raw media type dictionary.
        resolver: The reference resolver.
        scope: The current resolution scope.

    Returns:
        A MediaTypeIR instance.
    """
    if not isinstance(media_raw, dict):
        return MediaTypeIR(
            media_type="",
            schema=None,
            example=None,
            examples={},
            encoding={},
            source_pointer=None,
            raw={},
        )

    # Parse schema
    schema: SchemaIR | None = None
    schema_raw = media_raw.get("schema")
    if schema_raw and resolver:
        schema = parse_schema(schema_raw, resolver, scope, DiagnosticsIR([], [], [], []))

    # Parse examples
    examples_raw = media_raw.get("examples", {})
    examples: dict[str, ExampleIR] = {}
    if isinstance(examples_raw, dict):
        for ex_name, ex_raw in examples_raw.items():
            if isinstance(ex_raw, dict):
                examples[str(ex_name)] = ExampleIR(
                    name=str(ex_name),
                    summary=ex_raw.get("summary"),
                    description=ex_raw.get("description"),
                    value=ex_raw.get("value"),
                    external_value=ex_raw.get("externalValue"),
                    raw=ex_raw,
                )

    return MediaTypeIR(
        media_type="",
        schema=schema,
        example=media_raw.get("example"),
        examples=examples,
        encoding=media_raw.get("encoding", {}),
        source_pointer=None,
        raw=media_raw,
    )


def parse_media_type(
    media_type: str,
    media_raw: dict,
    resolver: ReferenceResolver | None,
    scope: str | None,
    diagnostics: DiagnosticsIR,
) -> MediaTypeIR:
    """
    Parse a media type object.

    Args:
        media_type: The media type string (e.g., application/json).
        media_raw: The raw media type dictionary.
        resolver: The reference resolver.
        scope: The current resolution scope.
        diagnostics: The diagnostics container.

    Returns:
        A MediaTypeIR instance.
    """
    if not isinstance(media_raw, dict):
        return MediaTypeIR(
            media_type=media_type,
            schema=None,
            example=None,
            examples={},
            encoding={},
            source_pointer=None,
            raw={},
        )

    # Parse schema
    schema: SchemaIR | None = None
    schema_raw = media_raw.get("schema")
    if schema_raw:
        try:
            schema = parse_schema(schema_raw, resolver, scope, diagnostics)
        except Exception as exc:
            diagnostics.spec_warnings.append(
                make_diagnostic(
                    severity="warning",
                    code="SCHEMA_PARSE_ERROR",
                    message=f"Failed to parse schema for {media_type}: {exc}",
                    exc=exc,
                )
            )

    # Parse examples
    examples_raw = media_raw.get("examples", {})
    examples: dict[str, ExampleIR] = {}
    if isinstance(examples_raw, dict):
        for ex_name, ex_raw in examples_raw.items():
            if isinstance(ex_raw, dict):
                examples[str(ex_name)] = ExampleIR(
                    name=str(ex_name),
                    summary=ex_raw.get("summary"),
                    description=ex_raw.get("description"),
                    value=ex_raw.get("value"),
                    external_value=ex_raw.get("externalValue"),
                    raw=ex_raw,
                )

    return MediaTypeIR(
        media_type=media_type,
        schema=schema,
        example=media_raw.get("example"),
        examples=examples,
        encoding=media_raw.get("encoding", {}),
        source_pointer=None,
        raw=media_raw,
    )


def parse_request_body(
    operation_raw: dict,
    adapter: SpecificationAdapter,
    resolver: ReferenceResolver,
    scope: str,
    diagnostics: DiagnosticsIR,
) -> RequestBodyIR | None:
    """
    Parse request body for an operation.

    Args:
        operation_raw: The raw operation dictionary.
        adapter: The specification adapter.
        resolver: The reference resolver.
        scope: The current resolution scope.
        diagnostics: The diagnostics container.

    Returns:
        A RequestBodyIR instance or None if no request body is defined.
    """
    raw_request_body = adapter.get_request_body_definition(operation_raw)
    if raw_request_body is None:
        return None

    # Handle $ref
    if "$ref" in raw_request_body:
        try:
            _, raw_request_body = resolver.resolve(raw_request_body["$ref"])
            if not isinstance(raw_request_body, dict):
                raise ValueError("Resolved requestBody is not an object")
        except Exception as exc:
            diagnostics.operation_errors.append(
                make_diagnostic(
                    severity="error",
                    code="REQUEST_BODY_REF_ERROR",
                    message=f"Failed to resolve requestBody $ref: {exc}",
                    exc=exc,
                )
            )
            return None

    if not isinstance(raw_request_body, dict):
        return None

    description = raw_request_body.get("description")
    required = bool(raw_request_body.get("required", False))
    raw_content = raw_request_body.get("content", {})

    contents: dict[str, MediaTypeIR] = {}
    if isinstance(raw_content, dict):
        for media_type, media_raw in raw_content.items():
            if isinstance(media_raw, dict):
                contents[media_type] = parse_media_type(
                    media_type=media_type,
                    media_raw=media_raw,
                    resolver=resolver,
                    scope=scope,
                    diagnostics=diagnostics,
                )

    return RequestBodyIR(
        required=required,
        description=description,
        contents=contents,
        source_pointer=None,
        raw=raw_request_body,
    )
