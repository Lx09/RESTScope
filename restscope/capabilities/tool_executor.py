"""Local tool execution after policy validation."""

from __future__ import annotations

from typing import Any

from restscope.capabilities.tool_call_validator import ToolCallValidator
from restscope.capabilities.tool_context import ToolContext, ToolContextError
from restscope.capabilities.tool_registry import ToolRegistry
from restscope.llm.schemas import ToolCall, ToolResult
from restscope.observability import TracingRuntime


class ToolExecutor:
    """Validate model tool calls, invoke registered handlers, and redact results.

    The registry says what tools exist, while the validator combines tool
    schema, role policy, and current Agent state. Only calls that pass that
    boundary reach a handler. Expected handler failures become structured
    ``ToolResult`` values so an Agent can reason about them safely.
    """

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
        """Return the App-bound target context, if startup has bound one."""
        return self._tool_context

    def bind_context(self, context: ToolContext) -> None:
        """Bind target URL, headers, and current IR exactly once."""
        if self._tool_context is not None:
            raise ToolContextError(
                "tool_context_already_initialized",
                "Tool context is already initialized",
            )
        self._tool_context = context

    def require_context(self) -> ToolContext:
        """Return the bound context or raise a stable startup-order error."""
        if self._tool_context is None:
            raise ToolContextError(
                "tool_context_not_initialized",
                "Tool context is not initialized",
            )
        return self._tool_context

    def clear_context(self) -> None:
        """Remove App-bound context during shutdown so it cannot be reused."""
        self._tool_context = None

    def execute(self, *, tool_call: ToolCall, role: str, state: dict) -> ToolResult:
        """
        Execute one bounded unit of work in the policy-controlled model tool boundary.

        The class owns any required collaborators or state; arguments supply only the
        data needed for this call.
        """
        with self.tracing_runtime.span(
            tool_call.name,
            kind="TOOL",
            input_value={"arguments": tool_call.arguments, "role": role},
            attributes={"tool.name": tool_call.name},
        ) as span:
            raw_result = self._execute(tool_call=tool_call, role=role, state=state)
            result = ToolResult.model_validate(
                self.tracing_runtime.redactor.redact(raw_result)
            )
            span.set_output(result)
            span.set_attribute("restscope.tool.status", result.status)
            if result.status in {"failed", "timed_out"}:
                message = (result.error or {}).get("message", result.status)
                span.mark_error(str(message))
            return result

    def _execute(self, *, tool_call: ToolCall, role: str, state: dict) -> ToolResult:
        """Apply policy first, then translate handler outcomes into one result shape."""

        # Validation precedes even handler lookup so a denied model call cannot
        # learn whether an unavailable or private implementation is registered.
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
            error = {"message": str(exc)}
            error_code = getattr(exc, "code", None)
            if isinstance(error_code, str) and error_code:
                error["code"] = error_code
            return ToolResult(
                tool_call_id=tool_call.id,
                name=tool_call.name,
                status="timed_out",
                error=error,
            )
        except Exception as exc:
            error = {"type": type(exc).__name__, "message": str(exc)}
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
