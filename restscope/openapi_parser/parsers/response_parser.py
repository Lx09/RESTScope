"""Response parser module."""

from ..adapters.base import SpecificationAdapter
from ..diagnostics import make_diagnostic
from ..ir import (
    DiagnosticsIR,
    ExampleIR,
    HeaderIR,
    LinkIR,
    MediaTypeIR,
    ResponseIR,
    ResponsesIR,
    SchemaIR,
    ServerIR,
)
from ..resolver import ReferenceResolver
from .request_body_parser import parse_media_type
from .schema_parser import parse_schema


def parse_response_headers(
    raw_headers: dict,
    resolver: ReferenceResolver,
    scope: str,
    diagnostics: DiagnosticsIR,
) -> dict[str, HeaderIR]:
    """
    Parse response headers.

    Args:
        raw_headers: The raw headers dictionary.
        resolver: The reference resolver.
        scope: The current resolution scope.
        diagnostics: The diagnostics container.

    Returns:
        Dictionary of header name to HeaderIR.
    """
    if not isinstance(raw_headers, dict):
        return {}

    result = {}
    for name, header_raw in raw_headers.items():
        if not isinstance(header_raw, dict):
            continue

        try:
            # Handle $ref
            if "$ref" in header_raw:
                _, header_raw = resolver.resolve(header_raw["$ref"])
                if not isinstance(header_raw, dict):
                    continue

            # Parse schema
            schema: SchemaIR | None = None
            schema_raw = header_raw.get("schema")
            if schema_raw:
                schema = parse_schema(schema_raw, resolver, scope, diagnostics)

            # Parse content
            content_raw = header_raw.get("content", {})
            content: dict[str, MediaTypeIR] = {}
            if isinstance(content_raw, dict):
                for media_type, media_raw in content_raw.items():
                    if isinstance(media_raw, dict):
                        content[media_type] = parse_media_type(
                            media_type=media_type,
                            media_raw=media_raw,
                            resolver=resolver,
                            scope=scope,
                            diagnostics=diagnostics,
                        )

            result[str(name)] = HeaderIR(
                name=str(name),
                description=header_raw.get("description"),
                required=bool(header_raw.get("required", False)),
                deprecated=bool(header_raw.get("deprecated", False)),
                allow_empty_value=bool(header_raw.get("allowEmptyValue", False)),
                style=header_raw.get("style"),
                explode=header_raw.get("explode"),
                schema=schema,
                content=content,
                raw=header_raw,
            )
        except Exception as exc:
            diagnostics.operation_errors.append(
                make_diagnostic(
                    severity="error",
                    code="HEADER_PARSE_ERROR",
                    message=f"Failed to parse header '{name}': {exc}",
                    exc=exc,
                )
            )

    return result


def parse_response_links(
    raw_links: dict,
    resolver: ReferenceResolver,
    scope: str,
    diagnostics: DiagnosticsIR,
) -> dict[str, LinkIR]:
    """
    Parse response links.

    Args:
        raw_links: The raw links dictionary.
        resolver: The reference resolver.
        scope: The current resolution scope.
        diagnostics: The diagnostics container.

    Returns:
        Dictionary of link name to LinkIR.
    """
    if not isinstance(raw_links, dict):
        return {}

    result = {}
    for name, link_raw in raw_links.items():
        if not isinstance(link_raw, dict):
            continue

        try:
            # Handle $ref
            if "$ref" in link_raw:
                _, link_raw = resolver.resolve(link_raw["$ref"])
                if not isinstance(link_raw, dict):
                    continue

            # Parse server
            server: ServerIR | None = None
            server_raw = link_raw.get("server")
            if isinstance(server_raw, dict):
                server = parse_server(server_raw)

            result[str(name)] = LinkIR(
                name=str(name),
                operation_ref=link_raw.get("operationRef"),
                operation_id=link_raw.get("operationId"),
                parameters=link_raw.get("parameters", {}),
                request_body=link_raw.get("requestBody"),
                description=link_raw.get("description"),
                server=server,
                raw=link_raw,
            )
        except Exception as exc:
            diagnostics.operation_errors.append(
                make_diagnostic(
                    severity="error",
                    code="LINK_PARSE_ERROR",
                    message=f"Failed to parse link '{name}': {exc}",
                    exc=exc,
                )
            )

    return result


def parse_server(server_raw: dict) -> ServerIR:
    """
    Parse a server definition.

    Args:
        server_raw: The raw server dictionary.

    Returns:
        A ServerIR instance.
    """
    from .server_parser import parse_server_variables

    variables_raw = server_raw.get("variables", {})
    variables = parse_server_variables(variables_raw)

    return ServerIR(
        url=server_raw.get("url", ""),
        description=server_raw.get("description"),
        variables=variables,
    )


def parse_response_contents(
    raw_response: dict,
    resolver: ReferenceResolver,
    scope: str,
    diagnostics: DiagnosticsIR,
) -> dict[str, MediaTypeIR]:
    """
    Parse response content.

    Handles both OpenAPI 3.x (content field) and Swagger 2.0 (schema field).

    Args:
        raw_response: The raw response dictionary.
        resolver: The reference resolver.
        scope: The current resolution scope.
        diagnostics: The diagnostics container.

    Returns:
        Dictionary of media type to MediaTypeIR.
    """
    content: dict[str, MediaTypeIR] = {}

    # OpenAPI 3.x style: content field
    content_raw = raw_response.get("content", {})
    if isinstance(content_raw, dict):
        for media_type, media_raw in content_raw.items():
            if isinstance(media_raw, dict):
                content[media_type] = parse_media_type(
                    media_type=media_type,
                    media_raw=media_raw,
                    resolver=resolver,
                    scope=scope,
                    diagnostics=diagnostics,
                )

    # Swagger 2.0 style: schema field directly
    if not content and "schema" in raw_response:
        schema_raw = raw_response.get("schema")
        if isinstance(schema_raw, dict):
            # Handle $ref
            if "$ref" in schema_raw:
                _, schema_raw = resolver.resolve(schema_raw["$ref"])
                if not isinstance(schema_raw, dict):
                    return content

            # Parse schema
            schema_ir = parse_schema(schema_raw, resolver, scope, diagnostics)

            # Use application/json as default media type for Swagger 2.0
            content["application/json"] = MediaTypeIR(
                media_type="application/json",
                schema=schema_ir,
                example=raw_response.get("example"),
                examples={},
                encoding={},
                source_pointer=None,
                raw={"schema": schema_raw},
            )

    return content


def parse_responses(
    operation_raw: dict,
    adapter: SpecificationAdapter,
    resolver: ReferenceResolver,
    scope: str,
    diagnostics: DiagnosticsIR,
) -> ResponsesIR:
    """
    Parse responses for an operation.

    Args:
        operation_raw: The raw operation dictionary.
        adapter: The specification adapter.
        resolver: The reference resolver.
        scope: The current resolution scope.
        diagnostics: The diagnostics container.

    Returns:
        A ResponsesIR instance.
    """
    raw_responses = adapter.get_responses_definition(operation_raw)
    if not isinstance(raw_responses, dict):
        raise ValueError("`responses` must be an object")

    parsed: dict[str, ResponseIR] = {}

    for status_code, raw_response in raw_responses.items():
        try:
            # Handle $ref
            if "$ref" in raw_response:
                _, raw_response = resolver.resolve(raw_response["$ref"])
                if not isinstance(raw_response, dict):
                    raise ValueError("Resolved response is not an object")

            headers = parse_response_headers(
                raw_response.get("headers", {}), resolver, scope, diagnostics
            )
            contents = parse_response_contents(
                raw_response, resolver, scope, diagnostics
            )
            links = parse_response_links(
                raw_response.get("links", {}), resolver, scope, diagnostics
            )

            parsed[str(status_code)] = ResponseIR(
                status_code=str(status_code),
                description=raw_response.get("description"),
                headers=headers,
                contents=contents,
                links=links,
                source_pointer=None,
                raw=raw_response,
            )
        except Exception as exc:
            diagnostics.operation_errors.append(
                make_diagnostic(
                    severity="error",
                    code="RESPONSE_PARSE_ERROR",
                    message=f"Failed to parse response {status_code}: {exc}",
                    path=None,
                    method=None,
                    pointer=None,
                    exc=exc,
                )
            )

    return ResponsesIR(by_status=parsed)
