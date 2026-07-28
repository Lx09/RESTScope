"""Role-specific tool safety policy."""

from __future__ import annotations

from restscope.capabilities.http_request import HTTP_REQUEST_TOOL_NAME
from restscope.llm.schemas import ToolSpec


class ToolPolicy:
    """Allow only explicitly safe tool use for each role."""

    MCP_READ_ROLES = {"planner", "result_analyst"}
    ROLE_ALLOWLISTS = {
        "planner": {
            "artifact.read_summary",
            "openapi.lookup_operation",
        },
        "result_analyst": {
            "artifact.read_summary",
            "observation.lookup_recent",
        },
    }

    def is_allowed(self, *, role: str, tool_spec: ToolSpec, state: dict) -> bool:
        """
        Return whether allowed applies in the policy-controlled model tool boundary.

        The class owns any required collaborators or state; arguments supply only the
        data needed for this call.
        """
        if tool_spec.name == "restscope.resource.lookup":
            return True
        if tool_spec.name == HTTP_REQUEST_TOOL_NAME:
            return True
        if tool_spec.requires_approval:
            return False
        if tool_spec.risk_level == "high":
            return False
        if tool_spec.kind == "mcp_tool":
            return role in self.MCP_READ_ROLES and tool_spec.read_only
        if role == "decision_maker":
            return tool_spec.read_only
        return tool_spec.name in self.ROLE_ALLOWLISTS.get(role, set())
