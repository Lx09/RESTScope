"""Expose RESTScope's small App-facing Interface without eager bootstrapping.

Importing a focused module such as ``restscope.llm`` should not construct the
App dependency graph or import every workflow.  The facade therefore initializes
logging once and resolves approved public entries only when callers request
them.  Internal Coordinators, Agents, factories, and Patch DTOs remain owned by
their workflow packages and are intentionally absent here.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

from .logging_config import get_logger, setup_logging


# Preserve the existing library behavior: importing RESTScope installs its
# logging defaults, while no database, LLM, or workflow object is constructed.
setup_logging()

__all__ = [
    "CONFIG",
    "OpenAPIParser",
    "OpenAPISpecIR",
    "OperationDocumentGenerationError",
    "OperationReference",
    "RESTScopeApp",
    "RESTScopeConfig",
    "RESTScopeRunReport",
    "RESTScopeRunRequest",
    "OpenAPICatalog",
    "OpenAPIChangeEventRecord",
    "OpenAPIChangeEventWrite",
    "build_generator_config_catalog",
    "build_openapi_document",
    "build_openapi_catalog",
    "get_logger",
    "setup_logging",
]


_PUBLIC_MODULE_BY_NAME = {
    "CONFIG": ".restscope_config",
    "OpenAPIParser": ".openapi_parser",
    "OpenAPISpecIR": ".openapi_parser",
    "OperationDocumentGenerationError": ".openapi_parser",
    "OperationReference": ".operations",
    "RESTScopeApp": ".app",
    "RESTScopeConfig": ".restscope_config",
    "RESTScopeRunReport": ".harness",
    "RESTScopeRunRequest": ".harness",
    "OpenAPICatalog": ".catalog",
    "OpenAPIChangeEventRecord": ".catalog",
    "OpenAPIChangeEventWrite": ".catalog",
    "build_generator_config_catalog": ".bootstrap",
    "build_openapi_document": ".openapi_parser",
    "build_openapi_catalog": ".bootstrap",
}


def __getattr__(name: str) -> Any:
    """Resolve one approved facade name from its owning module.

    Raises:
        AttributeError: The requested symbol is internal or does not exist.
    """
    module_name = _PUBLIC_MODULE_BY_NAME.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(module_name, __name__), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    """Show the supported facade Interface to readers and IDEs."""
    return sorted(set(globals()) | set(__all__))
