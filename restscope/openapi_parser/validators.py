"""Validators module for OpenAPI schema validation."""

from .constants import HTTP_METHODS
from .diagnostics import make_diagnostic
from .ir import DiagnosticsIR


def validate_top_level_schema(raw_schema: dict, adapter) -> None:
    """
    Validate the top-level schema structure.

    Delegates to the adapter for version-specific validation.

    Args:
        raw_schema: The raw schema dictionary.
        adapter: The specification adapter.

    Raises:
        InvalidTopLevelSchemaError: If the schema is invalid.
    """
    adapter.validate_top_level(raw_schema)


def validate_paths(raw_schema: dict, diagnostics: DiagnosticsIR) -> None:
    """
    Validate the paths section of the schema.

    Args:
        raw_schema: The raw schema dictionary.
        diagnostics: The diagnostics container to append errors to.
    """
    paths = raw_schema.get("paths", {})
    if not isinstance(paths, dict):
        diagnostics.spec_errors.append(
            make_diagnostic(
                severity="error",
                code="INVALID_PATHS",
                message="'paths' must be an object",
            )
        )
        return

    for path, path_item in paths.items():
        if not path.startswith("/"):
            diagnostics.spec_warnings.append(
                make_diagnostic(
                    severity="warning",
                    code="INVALID_PATH_KEY",
                    message=f"Path '{path}' should start with '/'",
                    path=path,
                )
            )

        if not isinstance(path_item, dict):
            diagnostics.path_errors.append(
                make_diagnostic(
                    severity="error",
                    code="INVALID_PATH_ITEM",
                    message=f"Path item for '{path}' must be an object or $ref",
                    path=path,
                )
            )
            continue

        # Check if it's a $ref
        if "$ref" in path_item:
            continue

        # Validate operations
        for method, operation in path_item.items():
            if method in ("summary", "description", "parameters", "servers", "$ref"):
                continue
            if method.lower() in HTTP_METHODS:
                if not isinstance(operation, dict):
                    diagnostics.operation_errors.append(
                        make_diagnostic(
                            severity="error",
                            code="INVALID_OPERATION",
                            message=f"Operation '{method.upper()} {path}' must be an object",
                            path=path,
                            method=method.lower(),
                        )
                    )
                    continue

                # Check for responses
                if "responses" not in operation:
                    diagnostics.operation_errors.append(
                        make_diagnostic(
                            severity="error",
                            code="MISSING_RESPONSES",
                            message=f"Operation '{method.upper()} {path}' is missing 'responses'",
                            path=path,
                            method=method.lower(),
                        )
                    )


def validate_schema_minimal(raw_schema: dict) -> None:
    """
    Perform minimal validation on the schema.

    Args:
        raw_schema: The raw schema dictionary.

    Raises:
        InvalidTopLevelSchemaError: If the schema is too invalid.
    """
    if not isinstance(raw_schema, dict):
        from .exceptions import InvalidTopLevelSchemaError
        raise InvalidTopLevelSchemaError("Top-level schema must be an object")

    # Check for version field
    has_swagger = "swagger" in raw_schema
    has_openapi = "openapi" in raw_schema

    if not has_swagger and not has_openapi:
        from .exceptions import InvalidTopLevelSchemaError
        raise InvalidTopLevelSchemaError(
            "Schema must have 'swagger' or 'openapi' field"
        )

    # Check for info
    if "info" not in raw_schema:
        from .exceptions import InvalidTopLevelSchemaError
        raise InvalidTopLevelSchemaError("Schema must have 'info' field")
