"""Parameter parser module."""

import re

from ..adapters.base import SpecificationAdapter
from ..constants import HTTP_METHODS
from ..diagnostics import make_diagnostic
from ..ir import DiagnosticsIR, ExampleIR, ParameterIR, SchemaIR
from ..resolver import ReferenceResolver
from .schema_parser import extract_legacy_inline_schema, parse_schema


def parse_single_parameter_from_definition(
    raw_param: dict,
    resolver: ReferenceResolver | None,
    scope: str | None,
    pointer: str | None = None,
    synthetic: bool = False,
) -> ParameterIR:
    """
    Parse a single parameter definition.

    Args:
        raw_param: The raw parameter dictionary.
        resolver: The reference resolver.
        scope: The current resolution scope.
        pointer: The JSON Pointer to this parameter.
        synthetic: Whether this is a synthetic (auto-generated) parameter.

    Returns:
        A ParameterIR instance.
    """
    # Handle $ref
    if "$ref" in raw_param:
        if resolver is None:
            raise ValueError("Resolver is required for $ref parameter")
        _, raw_param = resolver.resolve(raw_param["$ref"])
        if not isinstance(raw_param, dict):
            raise ValueError("Resolved parameter is not an object")

    name = raw_param.get("name", "")
    location = raw_param.get("in", "")
    required = bool(raw_param.get("required", False))

    # Path parameters must be required
    if location == "path":
        required = True

    deprecated = bool(raw_param.get("deprecated", False))
    allow_empty_value = bool(raw_param.get("allowEmptyValue", False))
    style = raw_param.get("style")
    explode = raw_param.get("explode")
    allow_reserved = bool(raw_param.get("allowReserved", False))

    description = raw_param.get("description")
    example = raw_param.get("example")

    # Parse examples
    examples_raw = raw_param.get("examples", {})
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

    # Parse content
    content_raw = raw_param.get("content", {})
    content: dict[str, object] = {}
    if isinstance(content_raw, dict):
        from .request_body_parser import parse_media_type_for_parameter
        for media_type, media_raw in content_raw.items():
            if isinstance(media_raw, dict):
                content[media_type] = parse_media_type_for_parameter(media_raw, resolver, scope)

    # Parse schema
    schema: SchemaIR | None = None
    schema_raw = raw_param.get("schema") or extract_legacy_inline_schema(raw_param)
    if schema_raw:
        schema = parse_schema(schema_raw, resolver, scope, DiagnosticsIR([], [], [], []), pointer)

    return ParameterIR(
        name=name,
        location=location,
        required=required,
        deprecated=deprecated,
        allow_empty_value=allow_empty_value,
        style=style,
        explode=explode,
        allow_reserved=allow_reserved,
        description=description,
        example=example,
        examples=examples,
        content=content,
        schema=schema,
        synthetic=synthetic,
        source_pointer=pointer,
        raw=raw_param,
    )


def parse_parameters(
    raw_parameters: list[dict],
    adapter: SpecificationAdapter,
    resolver: ReferenceResolver,
    scope: str,
    diagnostics: DiagnosticsIR,
    pointer: str | None,
) -> list[ParameterIR]:
    """
    Parse a list of parameter definitions.

    Args:
        raw_parameters: List of raw parameter dictionaries.
        adapter: The specification adapter.
        resolver: The reference resolver.
        scope: The current resolution scope.
        diagnostics: The diagnostics container.
        pointer: The JSON Pointer to these parameters.

    Returns:
        List of ParameterIR instances.
    """
    if not isinstance(raw_parameters, list):
        return []

    result = []
    seen_keys: set[tuple[str, str]] = set()

    for raw_param in raw_parameters:
        if not isinstance(raw_param, dict):
            continue

        # Skip body parameters in Swagger 2.0 (they are handled by request body parser)
        if adapter.spec_format == "swagger2":
            param_in = raw_param.get("in")
            if param_in == "body":
                continue
            # formData parameters are also handled by request body parser
            if param_in == "formData":
                continue

        try:
            param = parse_single_parameter_from_definition(
                raw_param=raw_param,
                resolver=resolver,
                scope=scope,
                pointer=pointer,
                synthetic=False,
            )

            # Deduplicate by (name, location)
            key = (param.name, param.location)
            if key in seen_keys:
                continue
            seen_keys.add(key)

            result.append(param)
        except Exception as exc:
            diagnostics.operation_errors.append(
                make_diagnostic(
                    severity="error",
                    code="PARAMETER_PARSE_ERROR",
                    message=f"Failed to parse parameter '{raw_param.get('name', 'unknown')}': {exc}",
                    pointer=pointer,
                    exc=exc,
                )
            )

    return result


def merge_parameters(
    shared_parameters: list[ParameterIR],
    operation_parameters: list[ParameterIR],
) -> list[ParameterIR]:
    """
    Merge shared (path-level) and operation-level parameters.

    Operation-level parameters override shared parameters with the same (name, location).

    Args:
        shared_parameters: Path-level shared parameters.
        operation_parameters: Operation-level parameters.

    Returns:
        Merged list of parameters.
    """
    merged: dict[tuple[str, str], ParameterIR] = {}

    for p in shared_parameters:
        merged[(p.name, p.location)] = p

    for p in operation_parameters:
        merged[(p.name, p.location)] = p

    return list(merged.values())


def get_template_fields(path: str) -> set[str]:
    """
    Extract variable names from a path template.

    Args:
        path: The path template (e.g., /users/{id}/posts/{post_id}).

    Returns:
        Set of variable names (e.g., {"id", "post_id"}).
    """
    return set(re.findall(r"\{([^}]+)\}", path))


def inject_missing_path_parameters(
    path: str,
    parameters: list[ParameterIR],
    adapter: SpecificationAdapter,
) -> list[ParameterIR]:
    """
    Inject synthetic path parameters for missing path variables.

    Args:
        path: The path template.
        parameters: Existing parameters.
        adapter: The specification adapter.

    Returns:
        Updated list of parameters with synthetic path parameters added.
    """
    existing = {p.name for p in parameters if p.location == "path"}
    required_names = get_template_fields(path)
    result = list(parameters)

    for name in sorted(required_names - existing):
        synthetic_def = adapter.build_synthetic_path_parameter(name)
        param = parse_single_parameter_from_definition(
            raw_param=synthetic_def,
            resolver=None,
            scope=None,
            pointer=None,
            synthetic=True,
        )
        result.append(param)

    return result
