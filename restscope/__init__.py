"""RESTScope - OpenAPI parsing utilities."""

from .logging_config import setup_logging, get_logger

# Auto-initialize logging when package is imported
setup_logging()

from .openapi_parser import (
    OpenAPIParser,
    OpenAPISpecIR,
    OperationDocumentGenerationError,
    build_openapi_document,
)
from .restscope_config import CONFIG, RESTScopeConfig
from .app import RESTScopeApp
from .catalog import (
    SchemaCatalog,
    SchemaNotFoundError,
    SchemaRecord,
    SchemaSourceInput,
    SchemaSourceValidationError,
)
from .bootstrap import build_schema_catalog
from .agent import (
    OperationReference,
    RESTScopeRunReport,
    RESTScopeRunRequest,
)

__all__ = [
    # Logging
    "setup_logging",
    "get_logger",
    # OpenAPI Parser
    "OpenAPIParser",
    "OpenAPISpecIR",
    "OperationDocumentGenerationError",
    "build_openapi_document",
    # Configuration
    "CONFIG",
    "RESTScopeConfig",
    # Program entry
    "RESTScopeApp",
    "SchemaCatalog",
    "SchemaNotFoundError",
    "SchemaRecord",
    "SchemaSourceInput",
    "SchemaSourceValidationError",
    "build_schema_catalog",
    "RESTScopeRunRequest",
    "RESTScopeRunReport",
    "OperationReference",
]
