"""Components parser module."""

from ..adapters.base import SpecificationAdapter
from ..constants import SPEC_FORMAT_SWAGGER2
from ..diagnostics import make_diagnostic
from ..ir import (
    ComponentsIR,
    DiagnosticsIR,
    ExampleIR,
    HeaderIR,
    LinkIR,
    ParameterIR,
    RequestBodyIR,
    ResponseIR,
    SchemaIR,
    SecuritySchemeIR,
)
from ..resolver import ReferenceResolver
from .parameter_parser import parse_single_parameter_from_definition
from .request_body_parser import parse_request_body
from .response_parser import parse_responses
from .schema_parser import extract_legacy_inline_schema, parse_schema
from .security_parser import parse_security_scheme


def parse_components(
    raw_schema: dict,
    adapter: SpecificationAdapter,
    resolver: ReferenceResolver,
    diagnostics: DiagnosticsIR,
) -> ComponentsIR:
    """
    Parse components section.

    Args:
        raw_schema: The raw schema dictionary.
        adapter: The specification adapter.
        resolver: The reference resolver.
        diagnostics: The diagnostics container.

    Returns:
        A ComponentsIR instance.
    """
    components_container = adapter.get_components_container(raw_schema)
    if not isinstance(components_container, dict):
        components_container = {}

    # Parse schemas
    schemas_raw = components_container.get("schemas", {})
    schemas: dict[str, SchemaIR] = {}
    if isinstance(schemas_raw, dict):
        for name, schema_raw in schemas_raw.items():
            try:
                # Pre-populate visited_refs with the schema's own ref path
                # to detect self-references
                if adapter.spec_format == SPEC_FORMAT_SWAGGER2:
                    schema_ref_path = f"#/definitions/{name}"
                else:
                    schema_ref_path = f"#/components/schemas/{name}"

                parsed = parse_schema(
                    schema_raw, resolver, None, diagnostics,
                    visited_refs={schema_ref_path}
                )
                if parsed is not None:
                    schemas[str(name)] = parsed
            except Exception as exc:  # noqa: BLE001
                diagnostics.spec_errors.append(
                    make_diagnostic(
                        severity="error",
                        code="COMPONENT_SCHEMA_PARSE_ERROR",
                        message=f"Failed to parse schema '{name}': {exc}",
                        exc=exc,
                    )
                )

    # Parse parameters
    parameters_raw = components_container.get("parameters", {})
    parameters: dict[str, ParameterIR] = {}
    if isinstance(parameters_raw, dict):
        for name, param_raw in parameters_raw.items():
            try:
                if isinstance(param_raw, dict):
                    parsed = parse_single_parameter_from_definition(
                        raw_param=param_raw,
                        resolver=resolver,
                        scope=None,
                        pointer=None,
                        synthetic=False,
                    )
                    parameters[str(name)] = parsed
            except Exception as exc:  # noqa: BLE001
                diagnostics.spec_errors.append(
                    make_diagnostic(
                        severity="error",
                        code="COMPONENT_PARAMETER_PARSE_ERROR",
                        message=f"Failed to parse parameter '{name}': {exc}",
                        exc=exc,
                    )
                )

    # Parse request bodies
    request_bodies_raw = components_container.get("requestBodies", {})
    request_bodies: dict[str, RequestBodyIR] = {}
    if isinstance(request_bodies_raw, dict):
        for name, rb_raw in request_bodies_raw.items():
            try:
                if isinstance(rb_raw, dict):
                    # Create a mock operation for parsing
                    mock_operation = {"requestBody": rb_raw}
                    parsed = parse_request_body(
                        operation_raw=mock_operation,
                        adapter=adapter,
                        resolver=resolver,
                        scope=None,
                        diagnostics=diagnostics,
                    )
                    if parsed is not None:
                        request_bodies[str(name)] = parsed
            except Exception as exc:  # noqa: BLE001
                diagnostics.spec_errors.append(
                    make_diagnostic(
                        severity="error",
                        code="COMPONENT_REQUEST_BODY_PARSE_ERROR",
                        message=f"Failed to parse requestBody '{name}': {exc}",
                        exc=exc,
                    )
                )

    # Parse responses
    responses_raw = components_container.get("responses", {})
    responses: dict[str, ResponseIR] = {}
    if isinstance(responses_raw, dict):
        for name, resp_raw in responses_raw.items():
            try:
                if isinstance(resp_raw, dict):
                    # Create a mock operation for parsing
                    mock_operation = {"responses": {str(name): resp_raw}}
                    parsed_responses = parse_responses(
                        operation_raw=mock_operation,
                        adapter=adapter,
                        resolver=resolver,
                        scope=None,
                        diagnostics=diagnostics,
                    )
                    if str(name) in parsed_responses.by_status:
                        responses[str(name)] = parsed_responses.by_status[str(name)]
            except Exception as exc:  # noqa: BLE001
                diagnostics.spec_errors.append(
                    make_diagnostic(
                        severity="error",
                        code="COMPONENT_RESPONSE_PARSE_ERROR",
                        message=f"Failed to parse response '{name}': {exc}",
                        exc=exc,
                    )
                )

    # Parse headers
    headers_raw = components_container.get("headers", {})
    headers: dict[str, HeaderIR] = {}
    if isinstance(headers_raw, dict):
        for name, header_raw in headers_raw.items():
            try:
                if isinstance(header_raw, dict):
                    # Handle $ref
                    if "$ref" in header_raw:
                        _, header_raw = resolver.resolve(header_raw["$ref"])
                        if not isinstance(header_raw, dict):
                            continue

                    # Parse schema
                    schema: SchemaIR | None = None
                    schema_raw = header_raw.get("schema") or extract_legacy_inline_schema(header_raw)
                    if schema_raw:
                        schema = parse_schema(schema_raw, resolver, None, diagnostics)

                    headers[str(name)] = HeaderIR(
                        name=str(name),
                        description=header_raw.get("description"),
                        required=bool(header_raw.get("required", False)),
                        deprecated=bool(header_raw.get("deprecated", False)),
                        allow_empty_value=bool(header_raw.get("allowEmptyValue", False)),
                        style=header_raw.get("style"),
                        explode=header_raw.get("explode"),
                        schema=schema,
                        content={},
                        raw=header_raw,
                    )
            except Exception as exc:  # noqa: BLE001
                diagnostics.spec_errors.append(
                    make_diagnostic(
                        severity="error",
                        code="COMPONENT_HEADER_PARSE_ERROR",
                        message=f"Failed to parse header '{name}': {exc}",
                        exc=exc,
                    )
                )

    # Parse security schemes
    security_schemes_raw = components_container.get("securitySchemes", {})
    security_schemes: dict[str, SecuritySchemeIR] = {}
    if isinstance(security_schemes_raw, dict):
        for name, scheme_raw in security_schemes_raw.items():
            try:
                if isinstance(scheme_raw, dict):
                    security_schemes[str(name)] = parse_security_scheme(
                        str(name), scheme_raw, spec_format=adapter.spec_format
                    )
            except Exception as exc:  # noqa: BLE001
                diagnostics.spec_errors.append(
                    make_diagnostic(
                        severity="error",
                        code="COMPONENT_SECURITY_SCHEME_PARSE_ERROR",
                        message=f"Failed to parse securityScheme '{name}': {exc}",
                        exc=exc,
                    )
                )

    # Parse examples
    examples_raw = components_container.get("examples", {})
    examples: dict[str, ExampleIR] = {}
    if isinstance(examples_raw, dict):
        for name, ex_raw in examples_raw.items():
            try:
                if isinstance(ex_raw, dict):
                    examples[str(name)] = ExampleIR(
                        name=str(name),
                        summary=ex_raw.get("summary"),
                        description=ex_raw.get("description"),
                        value=ex_raw.get("value"),
                        external_value=ex_raw.get("externalValue"),
                        raw=ex_raw,
                    )
            except Exception as exc:  # noqa: BLE001
                diagnostics.spec_errors.append(
                    make_diagnostic(
                        severity="error",
                        code="COMPONENT_EXAMPLE_PARSE_ERROR",
                        message=f"Failed to parse example '{name}': {exc}",
                        exc=exc,
                    )
                )

    # Parse links
    links_raw = components_container.get("links", {})
    links: dict[str, LinkIR] = {}
    if isinstance(links_raw, dict):
        for name, link_raw in links_raw.items():
            try:
                if isinstance(link_raw, dict):
                    # Handle $ref
                    if "$ref" in link_raw:
                        _, link_raw = resolver.resolve(link_raw["$ref"])
                        if not isinstance(link_raw, dict):
                            continue

                    links[str(name)] = LinkIR(
                        name=str(name),
                        operation_ref=link_raw.get("operationRef"),
                        operation_id=link_raw.get("operationId"),
                        parameters=link_raw.get("parameters", {}),
                        request_body=link_raw.get("requestBody"),
                        description=link_raw.get("description"),
                        server=None,
                        raw=link_raw,
                    )
            except Exception as exc:  # noqa: BLE001
                diagnostics.spec_errors.append(
                    make_diagnostic(
                        severity="error",
                        code="COMPONENT_LINK_PARSE_ERROR",
                        message=f"Failed to parse link '{name}': {exc}",
                        exc=exc,
                    )
                )

    # Parse callbacks (passthrough for now)
    callbacks_raw = components_container.get("callbacks", {})
    callbacks: dict[str, object] = {}
    if isinstance(callbacks_raw, dict):
        callbacks = dict(callbacks_raw)

    # Parse pathItems (passthrough for now)
    path_items_raw = components_container.get("pathItems", {})
    path_items: dict[str, object] = {}
    if isinstance(path_items_raw, dict):
        path_items = dict(path_items_raw)

    return ComponentsIR(
        schemas=schemas,
        parameters=parameters,
        request_bodies=request_bodies,
        responses=responses,
        headers=headers,
        security_schemes=security_schemes,
        examples=examples,
        links=links,
        callbacks=callbacks,
        path_items=path_items,
    )
