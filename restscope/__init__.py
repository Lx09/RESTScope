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
from .bootstrap import build_generator_config_catalog, build_schema_catalog
from .agent import (
    OpenAPIRetrievalAgent,
    OpenAPIRetrievalRequest,
    OpenAPIRetrievalResult,
    OperationReference,
    OperationSmokeAgent,
    OperationSmokeRequest,
    OperationSmokeResult,
    RESTScopeRunReport,
    RESTScopeRunRequest,
    ResourceLookupRequest,
    ResourceLookupResult,
    ResourceMonitorAgent,
    build_resource_monitor_agent,
    build_operation_smoke_agent,
    build_openapi_retrieval_agent,
    register_openapi_retrieval_tool,
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
    "build_generator_config_catalog",
    "build_openapi_retrieval_agent",
    "OpenAPIRetrievalAgent",
    "OpenAPIRetrievalRequest",
    "OpenAPIRetrievalResult",
    "RESTScopeRunRequest",
    "RESTScopeRunReport",
    "OperationReference",
    "OperationSmokeAgent",
    "OperationSmokeRequest",
    "OperationSmokeResult",
    "register_openapi_retrieval_tool",
    "ResourceLookupRequest",
    "ResourceLookupResult",
    "ResourceMonitorAgent",
    "build_resource_monitor_agent",
    "build_operation_smoke_agent",
]
