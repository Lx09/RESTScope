"""Operation runner abstractions and the Schemathesis MCP adapter."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import uuid4

from restscope.capabilities import ToolExecutor
from restscope.llm import ToolCall, ToolResult

from .schemas import FailureSummary, OperationExecutionResult, OperationTarget


class OperationTestRunner(Protocol):
    """Runner protocol used by OperationTestAgent."""

    def check_capabilities(self, *, target: OperationTarget, state: dict[str, Any]) -> dict[str, Any]: ...

    def run_operation(self, *, target: OperationTarget, state: dict[str, Any]) -> OperationExecutionResult: ...


@dataclass(frozen=True)
class FakeOperationTestCall:
    method: str
    path: str


class FakeOperationTestRunner:
    """Deterministic single-run adapter for unit tests."""

    def __init__(
        self,
        *,
        results: dict[tuple[str, str], OperationExecutionResult | list[OperationExecutionResult]] | None = None,
        error_paths: set[str] | None = None,
    ) -> None:
        self.results = results or {}
        self.error_paths = error_paths or set()
        self.calls: list[FakeOperationTestCall] = []

    def check_capabilities(self, *, target: OperationTarget, state: dict[str, Any]) -> dict[str, Any]:
        del target, state
        return {"runner": "fake", "available": True}

    def run_operation(self, *, target: OperationTarget, state: dict[str, Any]) -> OperationExecutionResult:
        del state
        operation = target.operation
        self.calls.append(FakeOperationTestCall(method=operation.method, path=operation.path))
        if operation.path in self.error_paths:
            raise RuntimeError(f"Fake operation error: {operation.path}")
        configured = self.results.get((operation.method, operation.path))
        if isinstance(configured, list):
            if configured:
                return configured.pop(0)
        elif configured is not None:
            return configured
        slug = f"{operation.method.lower()}_{operation.path.strip('/').replace('/', '_') or 'root'}"
        return OperationExecutionResult(
            run_id=f"fake_{slug}_run",
            outcome="passed",
            status_code_counts={"200": 1},
        )


class SchemathesisOperationRunner:
    """Run exactly one operation through Schemathesis MCP tools."""

    START_RUN = "mcp.schemathesis.start_run"
    GET_CAPABILITIES = "mcp.schemathesis.get_capabilities"
    GET_RUN = "mcp.schemathesis.get_run"
    GET_RESULT = "mcp.schemathesis.get_result"
    GET_FAILURE = "mcp.schemathesis.get_failure"

    def __init__(self, *, tool_executor: ToolExecutor, poll_interval: float = 0.5, poll_timeout: float = 180.0) -> None:
        self.tool_executor = tool_executor
        self.poll_interval = poll_interval
        self.poll_timeout = poll_timeout

    def check_capabilities(self, *, target: OperationTarget, state: dict[str, Any]) -> dict[str, Any]:
        del target
        result = self._execute_tool(name=self.GET_CAPABILITIES, arguments={}, state=state)
        return result.structured if isinstance(result.structured, dict) else {"content": result.content}

    def run_operation(self, *, target: OperationTarget, state: dict[str, Any]) -> OperationExecutionResult:
        start_result = self._execute_tool(
            name=self.START_RUN,
            arguments=self._start_run_arguments(target),
            state=state,
        )
        start_payload = start_result.structured if isinstance(start_result.structured, dict) else {}
        run_id = start_payload.get("run_id")
        if not run_id:
            raise RuntimeError("Schemathesis start_run did not return run_id")

        self._wait_for_run(run_id=str(run_id), state=state)
        result = self._execute_tool(name=self.GET_RESULT, arguments={"run_id": str(run_id)}, state=state)
        payload = result.structured if isinstance(result.structured, dict) else {}
        failure_ids = [str(item) for item in payload.get("failure_ids", [])]
        summaries = [self._get_failure_summary(str(run_id), failure_id, state) for failure_id in failure_ids[:20]]
        summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
        status_counts = summary.get("status_code_counts", {})
        return OperationExecutionResult(
            run_id=str(run_id),
            outcome=str(payload.get("outcome") or "errored"),
            status_code_counts={str(key): int(value) for key, value in status_counts.items()},
            failure_ids=failure_ids,
            failure_summaries=summaries,
            artifact_refs=_artifact_refs(payload.get("artifacts")),
            stop_reason=payload.get("stop_reason"),
        )

    def _execute_tool(self, *, name: str, arguments: dict[str, Any], state: dict[str, Any]) -> ToolResult:
        result = self.tool_executor.execute(
            tool_call=ToolCall(id=f"tool_call_{uuid4().hex}", name=name, arguments=arguments),
            role="operation_tester",
            state=state,
        )
        if result.status != "succeeded":
            raise RuntimeError(f"Tool {name} failed: {result.error or result.status}")
        return result

    @staticmethod
    def _start_run_arguments(target: OperationTarget) -> dict[str, Any]:
        arguments: dict[str, Any] = {
            "schema": target.schema_source,
            "base_url": target.base_url,
            "headers": target.headers or None,
            "include": {
                "path": target.operation.path,
                "method": target.operation.method,
            },
        }
        return {key: value for key, value in arguments.items() if value is not None}

    def _wait_for_run(self, *, run_id: str, state: dict[str, Any]) -> None:
        deadline = time.monotonic() + self.poll_timeout
        terminal_states = {"completed", "failed", "errored", "cancelled", "canceled", "expired", "finished"}
        while True:
            run_result = self._execute_tool(name=self.GET_RUN, arguments={"run_id": run_id}, state=state)
            payload = run_result.structured if isinstance(run_result.structured, dict) else {}
            run_state = str(payload.get("state") or payload.get("status") or "").lower()
            if run_state in terminal_states:
                return
            if time.monotonic() >= deadline:
                raise TimeoutError(f"Schemathesis run timed out: {run_id}")
            if self.poll_interval > 0:
                time.sleep(self.poll_interval)

    def _get_failure_summary(self, run_id: str, failure_id: str, state: dict[str, Any]) -> FailureSummary:
        result = self._execute_tool(
            name=self.GET_FAILURE,
            arguments={"run_id": run_id, "failure_id": failure_id},
            state=state,
        )
        payload = result.structured if isinstance(result.structured, dict) else {}
        response = payload.get("response") if isinstance(payload.get("response"), dict) else {}
        raw_status = response.get("status_code")
        return FailureSummary(
            failure_id=failure_id,
            check=str(payload.get("check") or "unknown_check"),
            title=str(payload.get("title") or "Schemathesis failure"),
            message=str(payload.get("message") or ""),
            response_status=int(raw_status) if raw_status is not None else None,
        )


def _artifact_refs(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        return [{"name": str(name), "uri": str(uri)} for name, uri in value.items()]
    if isinstance(value, list):
        return [item if isinstance(item, dict) else {"uri": str(item)} for item in value]
    if value is None:
        return []
    return [{"uri": str(value)}]
