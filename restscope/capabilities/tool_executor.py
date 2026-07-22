"""Local tool execution after policy validation."""

from __future__ import annotations

from typing import Any

from restscope.capabilities.tool_call_validator import ToolCallValidator
from restscope.capabilities.tool_context import ToolContext, ToolContextError
from restscope.capabilities.tool_registry import ToolRegistry
from restscope.llm.redactor import Redactor
from restscope.llm.schemas import ToolCall, ToolResult
from restscope.observability import TracingRuntime


class ToolExecutor:
    """Execute approved local handlers and summarize results."""

    def __init__(
        self,
        registry: ToolRegistry,
        validator: ToolCallValidator,
        artifact_service: Any | None = None,
        tracing_runtime: TracingRuntime | None = None,
    ) -> None:
        self.registry = registry
        self.validator = validator
        self.artifact_service = artifact_service
        self.tracing_runtime = tracing_runtime or TracingRuntime.disabled()
        self._tool_context: ToolContext | None = None

    @property
    def tool_context(self) -> ToolContext | None:
        return self._tool_context

    def bind_context(self, context: ToolContext) -> None:
        if self._tool_context is not None:
            raise ToolContextError(
                "tool_context_already_initialized",
                "Tool context is already initialized",
            )
        self._tool_context = context

    def require_context(self) -> ToolContext:
        if self._tool_context is None:
            raise ToolContextError(
                "tool_context_not_initialized",
                "Tool context is not initialized",
            )
        return self._tool_context

    def clear_context(self) -> None:
        self._tool_context = None

    def execute(self, *, tool_call: ToolCall, role: str, state: dict) -> ToolResult:
        with self.tracing_runtime.span(
            tool_call.name,
            kind="TOOL",
            input_value={"arguments": tool_call.arguments, "role": role},
            attributes={"tool.name": tool_call.name},
        ) as span:
            result = self._execute(tool_call=tool_call, role=role, state=state)
            span.set_output(result)
            span.set_attribute("restscope.tool.status", result.status)
            if result.status in {"failed", "timed_out"}:
                message = (result.error or {}).get("message", result.status)
                span.mark_error(str(message))
            return result

    def _execute(self, *, tool_call: ToolCall, role: str, state: dict) -> ToolResult:
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
            result = handler(self.require_context(), **tool_call.arguments)
        except TimeoutError as exc:
            return ToolResult(
                tool_call_id=tool_call.id,
                name=tool_call.name,
                status="timed_out",
                error={"message": self._redact_error(str(exc))},
            )
        except Exception as exc:
            error = {"type": type(exc).__name__, "message": self._redact_error(str(exc))}
            error_code = getattr(exc, "code", None)
            if isinstance(error_code, str) and error_code:
                error["code"] = error_code
            return ToolResult(
                tool_call_id=tool_call.id,
                name=tool_call.name,
                status="failed",
                error=error,
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

    def _redact_error(self, message: str) -> str:
        redacted = Redactor().redact_text(message)
        if self._tool_context is None:
            return redacted
        for value in self._tool_context.headers.values():
            if value:
                redacted = redacted.replace(value, "***REDACTED***")
        return redacted
