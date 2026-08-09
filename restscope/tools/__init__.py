"""Expose Tool catalog and execution primitives shared by every Tool subject.

Every RESTScope-owned model Tool is defined below this package. Agent Profiles
select names from the Catalog; the deterministic Harness binds only the live
implementations and session state required for that Agent. Subject-specific
contracts are imported from packages such as ``restscope.tools.openapi`` or
``restscope.tools.http`` so ownership remains visible at each call site.
"""

from .catalog import ToolCatalog, ToolDefinition, ToolSubject
from .runtime import AgentToolbox, ToolBinding, ToolFailure


def builtin_tool_catalog() -> ToolCatalog:
    """Load and return the built-in Catalog without eager workflow imports."""
    from .builtin import builtin_tool_catalog as build_catalog

    return build_catalog()


__all__ = [
    "ToolCatalog",
    "ToolDefinition",
    "ToolSubject",
    "AgentToolbox",
    "ToolBinding",
    "ToolFailure",
    "builtin_tool_catalog",
]
