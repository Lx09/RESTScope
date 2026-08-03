"""RESTScope capability and tool runtime."""

from .agent_tools import AgentToolbox, ToolFailure
from .http_request import (
    HTTP_REQUEST_TOOL_NAME,
    TargetHTTPRequestTool,
    http_request_tool_spec,
)
from .openapi_lookup import (
    OPENAPI_GET_INPUT_SCHEMA_TOOL_NAME,
    OPENAPI_GET_RESPONSE_FIELD_SCHEMA_TOOL_NAME,
    OPENAPI_LIST_INPUTS_TOOL_NAME,
    OpenAPICapability,
    operation_input_references,
    openapi_get_input_schema_tool_spec,
    openapi_get_response_field_schema_tool_spec,
    openapi_list_inputs_tool_spec,
)
from .runtime import CapabilityRuntime, build_capabilities, build_capabilities_with_mcp_host
from .skills import SkillManifest, SkillPolicy, SkillRegistry
from .tool_context import ToolContext, ToolContextError
from .tool_sources import (
    ToolSourceError,
    UnsupportedToolSourceKindError,
    register_tool_source,
)

__all__ = [
    "AgentToolbox",
    "ToolFailure",
    "CapabilityRuntime",
    "build_capabilities",
    "build_capabilities_with_mcp_host",
    "SkillManifest",
    "SkillPolicy",
    "SkillRegistry",
    "ToolContext",
    "ToolContextError",
    "HTTP_REQUEST_TOOL_NAME",
    "TargetHTTPRequestTool",
    "http_request_tool_spec",
    "OPENAPI_LIST_INPUTS_TOOL_NAME",
    "OPENAPI_GET_INPUT_SCHEMA_TOOL_NAME",
    "OPENAPI_GET_RESPONSE_FIELD_SCHEMA_TOOL_NAME",
    "OpenAPICapability",
    "operation_input_references",
    "openapi_list_inputs_tool_spec",
    "openapi_get_input_schema_tool_spec",
    "openapi_get_response_field_schema_tool_spec",
    "ToolSourceError",
    "UnsupportedToolSourceKindError",
    "register_tool_source",
]
