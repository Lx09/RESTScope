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
    CatalogInitializationError,
    OpenAPIInitializationRequest,
    OpenAPIInitializationResult,
    initialize_openapi_catalog,
)
from .agent import (
    OperationSelection,
    PlannerAgent,
    PlannerError,
    PlannerRequest,
    PlannerResult,
    RESTScopeRunReport,
    RESTScopeRunRequest,
    TestRequirement,
    TestRequirementPlan,
    build_planner_agent,
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
    "CatalogInitializationError",
    "OpenAPIInitializationRequest",
    "OpenAPIInitializationResult",
    "initialize_openapi_catalog",
    "RESTScopeRunRequest",
    "RESTScopeRunReport",
    "OperationSelection",
    "PlannerAgent",
    "PlannerError",
    "PlannerRequest",
    "PlannerResult",
    "TestRequirement",
    "TestRequirementPlan",
    "build_planner_agent",
]
