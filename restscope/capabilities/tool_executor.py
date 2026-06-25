"""Local tool execution after policy validation."""

from __future__ import annotations

from typing import Any

from restscope.capabilities.tool_call_validator import ToolCallValidator
from restscope.capabilities.tool_registry import ToolRegistry
from restscope.llm.schemas import ToolCall, ToolResult


class ToolExecutor:
    """Execute approved local handlers and summarize results."""

    def __init__(
        self,
        registry: ToolRegistry,
        validator: ToolCallValidator,
        artifact_service: Any | None = None,
    ) -> None:
        self.registry = registry
        self.validator = validator
        self.artifact_service = artifact_service

    def execute(self, *, tool_call: ToolCall, role: str, state: dict) -> ToolResult:
        errors = self.validator.validate(tool_call=tool_call, role=role, state=state)
        if errors:
            return ToolResult(
                tool_call_id=tool_call.id,
                name=tool_call.name,
                status="denied",
                error={"errors": errors},
            )

        spec = self.registry.get_spec(tool_call.name)
        try:
            handler = self.registry.get_handler(tool_call.name)
        except KeyError:
            return ToolResult(
                tool_call_id=tool_call.id,
                name=tool_call.name,
                status="failed",
                error={"type": "missing_handler", "message": f"No handler registered for {tool_call.name}"},
            )

        try:
            result = handler(**tool_call.arguments)
        except TimeoutError as exc:
            return ToolResult(
                tool_call_id=tool_call.id,
                name=tool_call.name,
                status="timed_out",
                error={"message": str(exc)},
            )
        except Exception as exc:
            return ToolResult(
                tool_call_id=tool_call.id,
                name=tool_call.name,
                status="failed",
                error={"type": type(exc).__name__, "message": str(exc)},
            )

        payload = result if isinstance(result, dict) else {"content": str(result)}
        return ToolResult(
            tool_call_id=tool_call.id,
            name=tool_call.name,
            status="succeeded",
            content=payload.get("content"),
            structured=payload.get("structured"),
            artifact_ids=payload.get("artifact_ids", []),
            metadata={
                "risk_level": spec.risk_level,
                "read_only": spec.read_only,
            },
        )
