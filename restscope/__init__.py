"""RESTScope - OpenAPI parsing utilities."""

from .logging_config import setup_logging, get_logger

# Auto-initialize logging when package is imported
setup_logging()

from .openapi_parser import OpenAPIParser, OpenAPISpecIR
from .restscope_config import CONFIG

__all__ = [
    # Logging
    "setup_logging",
    "get_logger",
    # OpenAPI Parser
    "OpenAPIParser",
    "OpenAPISpecIR",
    # Configuration
    "CONFIG",
]
