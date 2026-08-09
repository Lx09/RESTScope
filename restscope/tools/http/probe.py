"""Current-operation scope for model-requested Failure Resolution HTTP probes."""

from __future__ import annotations

from collections.abc import Callable
from http.cookies import CookieError, SimpleCookie
from typing import Any
from urllib.parse import unquote, urlsplit

from pydantic import ValidationError

from restscope.tools.context import ToolContext
from restscope.tools.http.request import (
    HTTP_REQUEST_TOOL_NAME,
    HTTPRequestArguments,
    HTTPRequestTimeoutError,
    HTTPRequestToolError,
    TargetHTTPRequestTool,
)
from restscope.llm import ToolCall, ToolResult
from restscope.tools.runtime import ToolBinding
from restscope.target_http import (
    TargetOperationIdentity,
    target_operation_scope,
)
from restscope.request_generation import OperationGeneratorConfig

class CurrentOperationHTTPProbe:
    """Scope the shared HTTP implementation to the current operation."""

    def __init__(
        self,
        *,
        http_tool: TargetHTTPRequestTool,
        context_provider: Callable[[], ToolContext],
    ) -> None:
        """Bind only the shared HTTP implementation and target context source."""
        self.http_tool = http_tool
        self.context_provider = context_provider

    def binding(self, config: OperationGeneratorConfig) -> ToolBinding:
        """Return the canonical HTTP Binding intercepted by Resolution.

        Resolution owns the active worklist checks and passes the complete
        ToolCall to :meth:`execute`. The global Catalog owns the shared Schema;
        operation-specific method and path checks use the supplied ``config``
        before any request can be sent.
        """
        del config
        return ToolBinding(
            name=HTTP_REQUEST_TOOL_NAME,
            execute=lambda **_arguments: {},
        )

    def execute(
        self,
        *,
        config: OperationGeneratorConfig,
        tool_call: ToolCall,
    ) -> ToolResult:
        """Validate and execute one model-requested diagnostic HTTP call.

        Args:
            config: The operation that the current Resolution session investigates.
            tool_call: The model's proposed call to the shared HTTP tool.

        Returns:
            The raw bounded HTTP Tool result. Operation Testing records it as a
            new ``TC*`` case and replaces response detail with parsed Failure
            evidence before the resolving Agent sees it.
        """
        error = _scope_error(config, tool_call)
        if error is not None:
            return ToolResult(
                tool_call_id=tool_call.id,
                name=tool_call.name,
                status="denied",
                error={
                    "code": "operation_smoke_probe_out_of_scope",
                    "message": error,
                },
            )
        with target_operation_scope(
            TargetOperationIdentity(
                operation_key=config.operation_key,
                method=config.snapshot.method,
                path=config.snapshot.path,
            )
        ):
            raw_result = self._send(tool_call)
        return raw_result

    def _send(self, tool_call: ToolCall) -> ToolResult:
        """Translate shared HTTP implementation outcomes into a raw result."""
        try:
            payload = self.http_tool.execute(
                self.context_provider(),
                **tool_call.arguments,
            )
        except HTTPRequestTimeoutError as exc:
            return ToolResult(
                tool_call_id=tool_call.id,
                name=tool_call.name,
                status="timed_out",
                error={"code": exc.code, "message": str(exc)},
            )
        except HTTPRequestToolError as exc:
            return ToolResult(
                tool_call_id=tool_call.id,
                name=tool_call.name,
                status="failed",
                error={"code": exc.code, "message": str(exc)},
            )
        return ToolResult(
            tool_call_id=tool_call.id,
            name=tool_call.name,
            status="succeeded",
            content=payload.get("content"),
            structured=payload.get("structured"),
            artifact_ids=payload.get("artifact_ids", []),
        )

    def validate(
        self,
        *,
        config: OperationGeneratorConfig,
        tool_call: ToolCall,
    ) -> str | None:
        """Return a scope error without executing any external request."""

        return _scope_error(config, tool_call)


def _scope_error(
    config: OperationGeneratorConfig,
    tool_call: ToolCall,
) -> str | None:
    """Explain why a probe would escape the current operation, if it would.

    The method must exactly match the operation, while concrete path-parameter
    values may replace template placeholders. Absolute URLs, query strings,
    fragments, path traversal, and encoded slashes are deliberately rejected.
    """
    if tool_call.name != HTTP_REQUEST_TOOL_NAME:
        return f"{tool_call.name} is not the allowed HTTP probe tool"
    try:
        HTTPRequestArguments.model_validate(tool_call.arguments)
    except ValidationError as exc:
        issue = exc.errors(include_input=False)[0]
        location = ".".join(
            str(item) for item in issue.get("loc", ())
        ) or "request"
        return (
            f"HTTP probe field {location} is invalid: "
            f"{issue['msg']}"
        )
    cookie_error = _probe_cookie_error(config, tool_call.arguments)
    if cookie_error is not None:
        return cookie_error
    method = tool_call.arguments.get("method")
    expected_method = config.snapshot.method.upper()
    if method != expected_method:
        return (
            f"HTTP probe method must be {expected_method} for the current "
            "operation"
        )
    path = tool_call.arguments.get("path")
    if not isinstance(path, str) or not _matches_path_template(
        path,
        config.snapshot.path,
    ):
        return (
            "HTTP probe path must match the current operation template "
            f"{config.snapshot.path}"
        )
    return None


def _probe_cookie_error(
    config: OperationGeneratorConfig,
    arguments: dict[str, Any],
) -> str | None:
    """Restrict a sensitive Cookie header to declared operation Parameters."""
    cookie_header = next(
        (
            value
            for name, value in (arguments.get("headers") or {}).items()
            if name.casefold() == "cookie"
        ),
        None,
    )
    if cookie_header is None:
        return None
    parsed = SimpleCookie()
    try:
        parsed.load(str(cookie_header))
    except CookieError:
        return "HTTP probe Cookie header is invalid"
    if not parsed:
        return "HTTP probe Cookie header must contain declared Cookie Parameters"
    declared = {
        item.name
        for item in config.snapshot.parameters
        if item.location == "cookie"
    }
    unknown = sorted(set(parsed) - declared)
    if unknown:
        return (
            "HTTP probe Cookie header contains undeclared Parameters: "
            + ", ".join(unknown)
        )
    return None


def _matches_path_template(path: str, template: str) -> bool:
    """Return whether a concrete safe path matches one OpenAPI path template."""

    parsed = urlsplit(path)
    if (
        parsed.scheme
        or parsed.netloc
        or parsed.query
        or parsed.fragment
        or not path.startswith("/")
        or path.startswith("//")
    ):
        return False
    actual_segments = path.split("/")
    template_segments = template.split("/")
    if len(actual_segments) != len(template_segments):
        return False
    for actual, expected in zip(actual_segments, template_segments):
        if expected.startswith("{") and expected.endswith("}"):
            decoded = unquote(actual)
            if not decoded or "/" in decoded or decoded in {".", ".."}:
                return False
            continue
        if unquote(actual) != unquote(expected):
            return False
    return True
