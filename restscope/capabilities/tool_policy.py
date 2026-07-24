"""Role-specific tool safety policy."""

from __future__ import annotations

from restscope.capabilities.http_request import HTTP_REQUEST_TOOL_NAME
from restscope.capabilities.testing_tools import (
    CONFIGURATION_TOOL_NAMES,
    RUN_OPERATION_TOOL_NAME,
)
from restscope.llm.schemas import ToolSpec


class ToolPolicy:
    """Allow only explicitly safe tool use for each role."""

    MCP_READ_ROLES = {"planner", "result_analyst", "operation_tester"}
    OPERATION_TESTER_LIVE_TOOLS = {"mcp.schemathesis.start_run"}
    ROLE_ALLOWLISTS = {
        "planner": {
            "artifact.read_summary",
            "openapi.lookup_operation",
            "schemathesis.validate_campaign_spec",
        },
        "result_analyst": {
            "artifact.read_summary",
            "observation.lookup_recent",
            "schemathesis.parse_result_summary",
        },
    }

    def is_allowed(self, *, role: str, tool_spec: ToolSpec, state: dict) -> bool:
        if tool_spec.name == "restscope.resource.lookup":
            return True
        if tool_spec.name in {HTTP_REQUEST_TOOL_NAME, RUN_OPERATION_TOOL_NAME}:
            return True
        if tool_spec.name in CONFIGURATION_TOOL_NAMES:
            return False

        if role == "operation_tester" and tool_spec.kind == "mcp_tool":
            if tool_spec.read_only:
                return True
            return tool_spec.name in self.OPERATION_TESTER_LIVE_TOOLS

        if tool_spec.requires_approval:
            return False
        if tool_spec.risk_level == "high":
            return False
        if tool_spec.kind == "mcp_tool":
            return role in self.MCP_READ_ROLES and tool_spec.read_only
        if role == "decision_maker":
            return tool_spec.read_only
        return tool_spec.name in self.ROLE_ALLOWLISTS.get(role, set())
