"""Current-operation scope for model-requested Failure Solve HTTP probes."""

from __future__ import annotations

from copy import deepcopy
from urllib.parse import unquote, urlsplit

from pydantic import ValidationError

from restscope.capabilities import ToolExecutor
from restscope.capabilities.http_request import (
    HTTP_REQUEST_TOOL_NAME,
    HTTPRequestArguments,
)
from restscope.llm import ToolCall, ToolResult, ToolSpec
from restscope.http_transport import (
    TargetOperationIdentity,
    target_operation_scope,
)
from restscope.testing import OperationGeneratorConfig


class CurrentOperationHTTPProbe:
    """Expose the global HTTP tool without granting cross-operation requests."""

    ROLE = "operation_smoke_failure_solve"

    def __init__(self, executor: ToolExecutor) -> None:
        self.executor = executor

    def tool_spec(self, config: OperationGeneratorConfig) -> ToolSpec:
        """
        Handle tool spec as part of the run-local Operation Smoke diagnosis and
        candidate workflow.

        The class owns any required collaborators or state; arguments supply only the
        data needed for this call.
        """
        source = self.executor.registry.get_spec(HTTP_REQUEST_TOOL_NAME)
        schema = deepcopy(source.input_schema)
        schema["properties"]["method"]["enum"] = [
            config.snapshot.method.upper()
        ]
        schema["properties"]["path"]["description"] = (
            "A concrete path matching the current operation template "
            f"{config.snapshot.path}. Replace only its path parameters."
        )
        return source.model_copy(
            update={
                "description": (
                    "Probe only the current operation "
                    f"{config.snapshot.method.upper()} "
                    f"{config.snapshot.path}. Context authentication is "
                    "injected automatically."
                ),
                "input_schema": schema,
                "metadata": {
                    **source.metadata,
                    "open_world": False,
                    "operation_key": config.operation_key,
                },
            }
        )

    def execute(
        self,
        *,
        config: OperationGeneratorConfig,
        tool_call: ToolCall,
    ) -> ToolResult:
        """
        Execute one bounded unit of work in the run-local Operation Smoke diagnosis and
        candidate workflow.

        The class owns any required collaborators or state; arguments supply only the
        data needed for this call.
        """
        error = _scope_error(config, tool_call)
        if error is not None:
            with self.executor.tracing_runtime.span(
                HTTP_REQUEST_TOOL_NAME,
                kind="TOOL",
                input_value={
                    "arguments": tool_call.arguments,
                    "role": self.ROLE,
                },
                attributes={"tool.name": HTTP_REQUEST_TOOL_NAME},
            ) as span:
                result = ToolResult(
                    tool_call_id=tool_call.id,
                    name=tool_call.name,
                    status="denied",
                    error={
                        "code": "operation_smoke_probe_out_of_scope",
                        "message": error,
                    },
                )
                result = ToolResult.model_validate(
                    self.executor.tracing_runtime.redactor.redact(result)
                )
                span.set_output(result)
                span.set_attribute("restscope.tool.status", result.status)
                return result
        with target_operation_scope(
            TargetOperationIdentity(
                operation_key=config.operation_key,
                method=config.snapshot.method,
                path=config.snapshot.path,
            )
        ):
            return self.executor.execute(
                tool_call=tool_call,
                role=self.ROLE,
                state={},
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
    """
    Handle scope error as part of the run-local Operation Smoke diagnosis and candidate
    workflow.

    This private helper keeps one transformation or policy decision explicit so the
    surrounding orchestration remains readable.
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


def _matches_path_template(path: str, template: str) -> bool:
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
