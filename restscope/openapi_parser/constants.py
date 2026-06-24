"""Constants used throughout the OpenAPI parser."""

from typing import FrozenSet

# HTTP methods
HTTP_METHODS: FrozenSet[str] = frozenset({
    "get", "put", "post", "delete", "options", "head", "patch", "trace"
})

# Parameter locations
PARAMETER_LOCATIONS: FrozenSet[str] = frozenset({
    "path", "query", "header", "cookie"
})

# Spec format identifiers
SPEC_FORMAT_SWAGGER2 = "swagger2"
SPEC_FORMAT_OAS30 = "openapi3.0"
SPEC_FORMAT_OAS31 = "openapi3.1"
SPEC_FORMAT_OAS32 = "openapi3.2"

# Source kinds
SOURCE_KIND_MEMORY = "memory"
SOURCE_KIND_FILE = "file"
SOURCE_KIND_URL = "url"

# Severity levels
SEVERITY_ERROR = "error"
SEVERITY_WARNING = "warning"
SEVERITY_INFO = "info"
