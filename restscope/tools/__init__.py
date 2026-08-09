"""Global Tool Catalog, execution runtime, and subject-specific Tool Modules.

Every RESTScope-owned model Tool is defined below this package. Agent Profiles
select names from the Catalog; the deterministic Harness binds only the live
implementations and session state required for that Agent.
"""

from .catalog import ToolCatalog, ToolDefinition, ToolSubject
from .context import ToolContext, ToolContextError
from .external import (
    ToolSourceError,
    UnsupportedToolSourceKindError,
    register_tool_source,
)
from .file import FILE_READ_TOOL_NAME, file_read_tool_binding, file_read_tool_spec
from .http import HTTP_REQUEST_TOOL_NAME, TargetHTTPRequestTool, http_request_tool_spec
from .openapi import (
    OPENAPI_FIND_OBSERVED_RESPONSE_FIELDS_TOOL_NAME,
    OPENAPI_GET_INPUT_SCHEMA_TOOL_NAME,
    OPENAPI_GET_RESPONSE_FIELD_SCHEMA_TOOL_NAME,
    OPENAPI_LIST_INPUTS_TOOL_NAME,
    OPENAPI_LIST_RESPONSE_FIELDS_TOOL_NAME,
    OpenAPICapability,
    openapi_find_observed_response_fields_tool_spec,
    openapi_get_input_schema_tool_spec,
    openapi_get_response_field_schema_tool_spec,
    openapi_list_inputs_tool_spec,
    openapi_list_response_fields_tool_spec,
    operation_input_references,
)
from .resource import (
    RESOURCE_LIST_IDS_TOOL_NAME,
    RESOURCE_LIST_RESOURCES_TOOL_NAME,
    ResourceIdentifierCapability,
    resource_list_ids_tool_spec,
    resource_list_resources_tool_spec,
)
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
    "ToolContext",
    "ToolContextError",
    "FILE_READ_TOOL_NAME",
    "file_read_tool_binding",
    "file_read_tool_spec",
    "HTTP_REQUEST_TOOL_NAME",
    "TargetHTTPRequestTool",
    "http_request_tool_spec",
    "OPENAPI_FIND_OBSERVED_RESPONSE_FIELDS_TOOL_NAME",
    "OPENAPI_GET_INPUT_SCHEMA_TOOL_NAME",
    "OPENAPI_GET_RESPONSE_FIELD_SCHEMA_TOOL_NAME",
    "OPENAPI_LIST_INPUTS_TOOL_NAME",
    "OPENAPI_LIST_RESPONSE_FIELDS_TOOL_NAME",
    "OpenAPICapability",
    "openapi_find_observed_response_fields_tool_spec",
    "openapi_get_input_schema_tool_spec",
    "openapi_get_response_field_schema_tool_spec",
    "openapi_list_inputs_tool_spec",
    "openapi_list_response_fields_tool_spec",
    "operation_input_references",
    "RESOURCE_LIST_IDS_TOOL_NAME",
    "RESOURCE_LIST_RESOURCES_TOOL_NAME",
    "ResourceIdentifierCapability",
    "resource_list_ids_tool_spec",
    "resource_list_resources_tool_spec",
    "ToolSourceError",
    "UnsupportedToolSourceKindError",
    "register_tool_source",
    "builtin_tool_catalog",
]
