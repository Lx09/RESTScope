"""Read-only resource lookup tool registration."""

from __future__ import annotations

from typing import Any

from restscope.capabilities.tool_context import ToolContext
from restscope.capabilities.tool_registry import ToolRegistry
from restscope.llm import ToolSpec

from .agent import ResourceMonitorAgent
from .schemas import ResourceLookupRequest, ResourceLookupResult


RESOURCE_LOOKUP_TOOL_NAME = "restscope.resource.lookup"


def register_resource_lookup_tool(
    registry: ToolRegistry,
    agent: ResourceMonitorAgent,
) -> ToolSpec:
    """Register deterministic lookup against the synchronously updated catalog."""

    spec = ToolSpec(
        name=RESOURCE_LOOKUP_TOOL_NAME,
        description=(
            "Look up typed resource identifiers and the API operations that "
            "most recently read or wrote them."
        ),
        kind="local_function",
        input_schema=ResourceLookupRequest.model_json_schema(),
        output_schema=ResourceLookupResult.model_json_schema(),
        risk_level="medium",
        read_only=True,
        requires_approval=False,
        timeout_seconds=30,
    )

    def lookup_handler(
        context: ToolContext,
        /,
        **arguments: Any,
    ) -> dict[str, Any]:
        del context
        request = ResourceLookupRequest.model_validate(arguments)
        result = agent.lookup(request)
        return {
            "content": (
                f"Resource {request.resource}: {result.total} identifier(s)"
                if result.status == "found"
                else f"Resource {request.resource}: not found"
            ),
            "structured": result.model_dump(mode="json"),
        }

    registry.register(spec=spec, handler=lookup_handler)
    return spec
