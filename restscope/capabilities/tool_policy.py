"""Role-specific tool safety policy."""

from __future__ import annotations

from restscope.llm.schemas import ToolSpec


class ToolPolicy:
    """Allow only explicitly safe tool use for each role."""

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
        del state
        if tool_spec.requires_approval:
            return False
        if tool_spec.risk_level == "high":
            return False
        if role == "decision_maker":
            return tool_spec.read_only
        return tool_spec.name in self.ROLE_ALLOWLISTS.get(role, set())
