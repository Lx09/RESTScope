"""Constants used throughout the OpenAPI parser."""


# HTTP methods
HTTP_METHODS: frozenset[str] = frozenset({
    "get", "put", "post", "delete", "options", "head", "patch", "trace"
})

# Parameter locations
PARAMETER_LOCATIONS: frozenset[str] = frozenset({
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
