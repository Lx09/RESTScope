"""Default read-only tool specifications for the LLM MVP."""

from __future__ import annotations

from restscope.llm.schemas import ToolSpec


def default_tool_specs() -> list[ToolSpec]:
    """Return the built-in read-only tool specs without registering handlers."""

    return [
        ToolSpec(
            name="artifact.read_summary",
            description="Read a short summarized view of an artifact.",
            kind="local_function",
            input_schema={
                "type": "object",
                "properties": {"artifact_id": {"type": "string"}},
                "required": ["artifact_id"],
            },
            risk_level="low",
            read_only=True,
        ),
        ToolSpec(
            name="openapi.lookup_operation",
            description="Look up a summarized operation card by operation_id.",
            kind="local_function",
            input_schema={
                "type": "object",
                "properties": {"operation_id": {"type": "string"}},
                "required": ["operation_id"],
            },
            risk_level="low",
            read_only=True,
        ),
        ToolSpec(
            name="observation.lookup_recent",
            description="Read recent observation summaries for an operation.",
            kind="local_function",
            input_schema={
                "type": "object",
                "properties": {
                    "operation_id": {"type": "string"},
                    "limit": {"type": "integer", "default": 5},
                },
                "required": ["operation_id"],
            },
            risk_level="low",
            read_only=True,
        ),
        ToolSpec(
            name="schemathesis.validate_campaign_spec",
            description="Validate whether a TestCampaignSpec can be mapped to runner configuration.",
            kind="local_function",
            input_schema={
                "type": "object",
                "properties": {"campaign_spec": {"type": "object"}},
                "required": ["campaign_spec"],
            },
            risk_level="low",
            read_only=True,
        ),
    ]
