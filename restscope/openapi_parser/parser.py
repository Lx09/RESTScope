"""Main parser module for OpenAPI specifications."""

from .adapters.base import SpecificationAdapter
from .constants import HTTP_METHODS
from .diagnostics import make_diagnostic
from .exceptions import InvalidTopLevelSchemaError
from .ir import (
    ComponentsIR,
    DiagnosticsIR,
    OpenAPISpecIR,
    OperationIR,
    PathItemIR,
    SpecIndexesIR,
    SpecMetaIR,
)
from .loader import load_parse_input
from .parsers import (
    parse_components,
    parse_meta,
    parse_operation_security,
    parse_parameters,
    parse_request_body,
    parse_responses,
)
from .parsers.parameter_parser import merge_parameters, inject_missing_path_parameters
from .parsers.server_parser import resolve_operation_servers
from .postprocess import (
    build_constraint_tags,
    build_resource_index,
    build_value_flow_indexes,
)
from .resolver import ReferenceResolver
from .versioning import detect_spec_version_and_adapter


def parse_path_item(
    path: str,
    path_item_raw: dict,
    adapter: SpecificationAdapter,
    resolver: ReferenceResolver,
    diagnostics: DiagnosticsIR,
) -> tuple[PathItemIR, dict, str]:
    """
    Parse a path item.

    Args:
        path: The path template.
        path_item_raw: The raw path item dictionary.
        adapter: The specification adapter.
        resolver: The reference resolver.
        diagnostics: The diagnostics container.

    Returns:
        Tuple of (PathItemIR, resolved dict, scope).
    """
    try:
        # Handle $ref
        if "$ref" in path_item_raw:
            scope, resolved = resolver.resolve(path_item_raw["$ref"])
            if not isinstance(resolved, dict):
                raise ValueError("Resolved path item is not an object")
        else:
            scope = resolver.resolution_scope
            resolved = path_item_raw

        # Parse shared parameters
        shared_parameters_raw = resolved.get("parameters", [])
        shared_parameters = parse_parameters(
            raw_parameters=shared_parameters_raw,
            adapter=adapter,
            resolver=resolver,
            scope=scope,
            diagnostics=diagnostics,
            pointer=None,
        )

        # Build PathItemIR
        path_item_ir = PathItemIR(
            path=path,
            summary=resolved.get("summary"),
            description=resolved.get("description"),
            shared_parameters=shared_parameters,
            operations={},
            extensions=collect_extensions(resolved),
        )

        return path_item_ir, resolved, scope

    except Exception as exc:
        diagnostics.path_errors.append(
            make_diagnostic(
                severity="error",
                code="PATH_ITEM_PARSE_ERROR",
                message=str(exc),
                path=path,
                method=None,
                pointer=None,
                exc=exc,
            )
        )
        raise


def collect_extensions(obj: dict) -> dict[str, object]:
    """
    Collect extension fields (x-*) from an object.

    Args:
        obj: The object to extract extensions from.

    Returns:
        Dictionary of extension fields.
    """
    if not isinstance(obj, dict):
        return {}

    return {k: v for k, v in obj.items() if k.startswith("x-")}


def parse_operation(
    path: str,
    method: str,
    operation_raw: dict,
    shared_parameters: list,
    raw_schema: dict,
    adapter: SpecificationAdapter,
    resolver: ReferenceResolver,
    scope: str,
    diagnostics: DiagnosticsIR,
) -> OperationIR:
    """
    Parse an operation.

    Args:
        path: The path template.
        method: The HTTP method.
        operation_raw: The raw operation dictionary.
        shared_parameters: Path-level shared parameters.
        raw_schema: The full schema dictionary.
        adapter: The specification adapter.
        resolver: The reference resolver.
        scope: The current resolution scope.
        diagnostics: The diagnostics container.

    Returns:
        An OperationIR instance.
    """
    # Parse operation-level parameters
    operation_parameters_raw = operation_raw.get("parameters", [])
    operation_parameters = parse_parameters(
        raw_parameters=operation_parameters_raw,
        adapter=adapter,
        resolver=resolver,
        scope=scope,
        diagnostics=diagnostics,
        pointer=None,
    )

    # Merge parameters (operation overrides path-level)
    merged_parameters = merge_parameters(shared_parameters, operation_parameters)

    # Inject missing path parameters
    merged_parameters = inject_missing_path_parameters(
        path=path,
        parameters=merged_parameters,
        adapter=adapter,
    )

    # Parse request body
    request_body = parse_request_body(
        operation_raw=operation_raw,
        adapter=adapter,
        resolver=resolver,
        scope=scope,
        diagnostics=diagnostics,
    )

    # Parse responses
    responses = parse_responses(
        operation_raw=operation_raw,
        adapter=adapter,
        resolver=resolver,
        scope=scope,
        diagnostics=diagnostics,
    )

    # Parse security
    security = parse_operation_security(
        raw_schema=raw_schema,
        operation_raw=operation_raw,
        adapter=adapter,
        resolver=resolver,
        diagnostics=diagnostics,
    )

    # Resolve servers
    servers = resolve_operation_servers(
        raw_schema=raw_schema,
        operation_raw=operation_raw,
        adapter=adapter,
    )

    # Collect extensions
    extensions = collect_extensions(operation_raw)

    # Build OperationIR
    return OperationIR(
        operation_key=f"{method.upper()} {path}",
        operation_id=operation_raw.get("operationId"),
        path=path,
        method=method,
        tags=ensure_list_of_str(operation_raw.get("tags", [])),
        summary=operation_raw.get("summary"),
        description=operation_raw.get("description"),
        deprecated=bool(operation_raw.get("deprecated", False)),
        path_parameters=[p for p in merged_parameters if p.location == "path"],
        query_parameters=[p for p in merged_parameters if p.location == "query"],
        header_parameters=[p for p in merged_parameters if p.location == "header"],
        cookie_parameters=[p for p in merged_parameters if p.location == "cookie"],
        request_body=request_body,
        responses=responses,
        security=security,
        servers=servers,
        callbacks=operation_raw.get("callbacks", {}) or {},
        links={},
        extensions=extensions,
        diagnostics=[],
    )


def ensure_list_of_str(value) -> list[str]:
    """
    Ensure a value is a list of strings.

    Args:
        value: The value to convert.

    Returns:
        List of strings.
    """
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v) for v in value]
    return [str(value)]


def build_operation_indexes(ir: OpenAPISpecIR) -> None:
    """
    Build operation indexes.

    Args:
        ir: The OpenAPISpecIR instance to update.
    """
    for op_key, op in ir.operations.items():
        ir.indexes.by_method_path[(op.method, op.path)] = op_key
        if op.operation_id:
            if op.operation_id in ir.indexes.by_operation_id:
                # Duplicate operationId - log warning
                ir.diagnostics.spec_warnings.append(
                    make_diagnostic(
                        severity="warning",
                        code="DUPLICATE_OPERATION_ID",
                        message=f"Duplicate operationId '{op.operation_id}'",
                    )
                )
            else:
                ir.indexes.by_operation_id[op.operation_id] = op_key


class OpenAPIParser:
    """
    Main parser for OpenAPI specifications.

    Supports Swagger 2.0 and OpenAPI 3.x specifications.
    """

    @staticmethod
    def parse(source: object) -> OpenAPISpecIR:
        """
        Parse an OpenAPI specification.

        Args:
            source: The input source (dict, YAML/JSON string, file path, or URL).

        Returns:
            An OpenAPISpecIR instance.
        """
        # Load input
        parse_input = load_parse_input(source)
        raw_schema = parse_input.raw_document

        # Detect version and get adapter
        spec_format, spec_version, adapter = detect_spec_version_and_adapter(raw_schema)

        # Validate top-level schema
        adapter.validate_top_level(raw_schema)

        # Initialize resolver
        resolver = ReferenceResolver(
            root_location=parse_input.source_location,
            root_document=raw_schema,
        )

        # Initialize diagnostics
        diagnostics = DiagnosticsIR(
            spec_errors=[],
            spec_warnings=[],
            path_errors=[],
            operation_errors=[],
        )

        # Parse metadata
        meta = parse_meta(
            raw_schema=raw_schema,
            adapter=adapter,
            spec_format=spec_format,
            spec_version=spec_version,
        )

        # Parse components
        components = parse_components(
            raw_schema=raw_schema,
            adapter=adapter,
            resolver=resolver,
            diagnostics=diagnostics,
        )

        # Initialize IR
        ir = OpenAPISpecIR(
            meta=meta,
            components=components,
            paths={},
            operations={},
            indexes=SpecIndexesIR(
                by_operation_id={},
                by_method_path={},
                resources={},
                constraint_tags=[],
                operation_resource_map={},
            ),
            diagnostics=diagnostics,
        )

        # Parse paths
        raw_paths = raw_schema.get("paths", {})
        if not isinstance(raw_paths, dict):
            raise InvalidTopLevelSchemaError("`paths` must be an object.")

        for path, path_item_raw in raw_paths.items():
            try:
                path_item_ir, resolved_path_item, scope = parse_path_item(
                    path=path,
                    path_item_raw=path_item_raw,
                    adapter=adapter,
                    resolver=resolver,
                    diagnostics=diagnostics,
                )
            except Exception:
                # Skip this path on error
                continue

            # Parse operations
            for method, operation_raw in resolved_path_item.items():
                if method not in HTTP_METHODS:
                    continue
                if not operation_raw:
                    continue
                if not isinstance(operation_raw, dict):
                    continue

                try:
                    op = parse_operation(
                        path=path,
                        method=method,
                        operation_raw=operation_raw,
                        shared_parameters=path_item_ir.shared_parameters,
                        raw_schema=raw_schema,
                        adapter=adapter,
                        resolver=resolver,
                        scope=scope,
                        diagnostics=diagnostics,
                    )
                    ir.operations[op.operation_key] = op
                    path_item_ir.operations[method] = op.operation_key
                except Exception as exc:
                    diagnostics.operation_errors.append(
                        make_diagnostic(
                            severity="error",
                            code="OPERATION_PARSE_ERROR",
                            message=str(exc),
                            path=path,
                            method=method,
                            pointer=None,
                            exc=exc,
                        )
                    )

            ir.paths[path] = path_item_ir

        # Build indexes
        build_operation_indexes(ir)
        build_resource_index(ir)
        build_constraint_tags(ir)
        build_value_flow_indexes(ir)

        return ir
