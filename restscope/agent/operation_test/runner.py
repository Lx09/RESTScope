"""Operation test runner abstractions and Schemathesis MCP scaffold."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import uuid4

from restscope.capabilities import ToolExecutor
from restscope.llm import ToolCall, ToolResult

from .schemas import OperationTarget, OperationTestFinding, OperationTestStageResult, StageOptions
from .stages import OperationTestStage


class OperationTestRunner(Protocol):
    """Runner protocol used by OperationTestAgent graph nodes."""

    def check_capabilities(self, *, target: OperationTarget, state: dict[str, Any]) -> dict[str, Any]:
        """Return runner capability metadata, or raise when unavailable."""

    def run_stage(
        self,
        *,
        stage: OperationTestStage,
        target: OperationTarget,
        options: StageOptions,
        state: dict[str, Any],
    ) -> OperationTestStageResult:
        """Run one testing stage and return a sanitized summary."""


@dataclass(frozen=True)
class FakeOperationTestCall:
    """Recorded fake runner invocation for tests."""

    stage: str
    method: str
    path: str


class FakeOperationTestRunner:
    """Deterministic runner for graph and report tests."""

    def __init__(self, *, fail_stage: str | None = None, failed_stage: str | None = None) -> None:
        self.fail_stage = fail_stage
        self.failed_stage = failed_stage
        self.calls: list[FakeOperationTestCall] = []

    def check_capabilities(self, *, target: OperationTarget, state: dict[str, Any]) -> dict[str, Any]:
        del target, state
        return {"runner": "fake", "available": True}

    def run_stage(
        self,
        *,
        stage: OperationTestStage,
        target: OperationTarget,
        options: StageOptions,
        state: dict[str, Any],
    ) -> OperationTestStageResult:
        del options, state
        self.calls.append(FakeOperationTestCall(stage=stage.name, method=target.method, path=target.path))
        if stage.name == self.fail_stage:
            raise RuntimeError(f"Fake stage error: {stage.name}")

        run_id = f"fake_{stage.name}_run"
        if stage.name == self.failed_stage:
            finding = OperationTestFinding(
                stage=stage.name,
                severity="high",
                title=f"{stage.name} mismatch",
                summary=f"Fake runner reported a mismatch during {stage.name}.",
                evidence_refs=[run_id],
            )
            return OperationTestStageResult(
                stage=stage.name,
                status="failed",
                run_id=run_id,
                outcome="failed",
                summary={"runner": "fake", "checks": {"failed": 1}},
                failure_ids=[f"{run_id}_failure"],
                findings=[finding],
            )

        return OperationTestStageResult(
            stage=stage.name,
            status="passed",
            run_id=run_id,
            outcome="passed",
            summary={"runner": "fake", "checks": {"passed": 1}},
        )


class SchemathesisOperationRunner:
    """Run operation stages through Schemathesis MCP tools via ToolExecutor."""

    START_RUN = "mcp.schemathesis.start_run"
    GET_CAPABILITIES = "mcp.schemathesis.get_capabilities"
    GET_RUN = "mcp.schemathesis.get_run"
    GET_RESULT = "mcp.schemathesis.get_result"

    def __init__(self, *, tool_executor: ToolExecutor, poll_interval: float = 0.5, poll_timeout: float = 180.0) -> None:
        self.tool_executor = tool_executor
        self.poll_interval = poll_interval
        self.poll_timeout = poll_timeout

    def check_capabilities(self, *, target: OperationTarget, state: dict[str, Any]) -> dict[str, Any]:
        del target
        result = self._execute_tool(name=self.GET_CAPABILITIES, arguments={}, state=state)
        return result.structured if isinstance(result.structured, dict) else {"content": result.content}

    def run_stage(
        self,
        *,
        stage: OperationTestStage,
        target: OperationTarget,
        options: StageOptions,
        state: dict[str, Any],
    ) -> OperationTestStageResult:
        start_result = self._execute_tool(
            name=self.START_RUN,
            arguments=self._start_run_arguments(stage=stage, target=target, options=options),
            state=state,
        )
        start_payload = start_result.structured if isinstance(start_result.structured, dict) else {}
        run_id = start_payload.get("run_id")
        if not run_id:
            raise RuntimeError("Schemathesis start_run did not return run_id")

        self._wait_for_run(run_id=str(run_id), options=options, state=state)
        result = self._execute_tool(name=self.GET_RESULT, arguments={"run_id": str(run_id)}, state=state)
        payload = result.structured if isinstance(result.structured, dict) else {}
        return self._stage_result_from_payload(stage=stage, run_id=str(run_id), payload=payload)

    def _execute_tool(self, *, name: str, arguments: dict[str, Any], state: dict[str, Any]) -> ToolResult:
        result = self.tool_executor.execute(
            tool_call=ToolCall(id=f"tool_call_{uuid4().hex}", name=name, arguments=arguments),
            role="operation_tester",
            state=state,
        )
        if result.status != "succeeded":
            raise RuntimeError(f"Tool {name} failed: {result.error or result.status}")
        return result

    def _start_run_arguments(
        self,
        *,
        stage: OperationTestStage,
        target: OperationTarget,
        options: StageOptions,
    ) -> dict[str, Any]:
        max_examples = options.boundary_max_examples if stage.name == "boundary" else options.max_examples
        if stage.max_examples_override is not None:
            max_examples = stage.max_examples_override

        arguments: dict[str, Any] = {
            "schema": target.schema_source,
            "base_url": target.base_url,
            "headers": target.headers or None,
            "phases": stage.phases,
            "checks": stage.checks or None,
            "generation_modes": stage.generation_modes,
            "include": {"path": target.path, "method": target.method.upper()},
            "max_examples": max_examples,
            "max_failures": options.max_failures,
            "max_time": options.max_time,
            "seed": options.seed,
        }
        return {key: value for key, value in arguments.items() if value is not None}

    def _wait_for_run(self, *, run_id: str, options: StageOptions, state: dict[str, Any]) -> None:
        timeout = options.poll_timeout if options.poll_timeout is not None else self.poll_timeout
        interval = options.poll_interval if options.poll_interval is not None else self.poll_interval
        deadline = time.monotonic() + timeout
        terminal_states = {"completed", "failed", "errored", "cancelled", "canceled", "expired", "finished"}

        while True:
            run_result = self._execute_tool(name=self.GET_RUN, arguments={"run_id": run_id}, state=state)
            payload = run_result.structured if isinstance(run_result.structured, dict) else {}
            run_state = str(payload.get("state") or payload.get("status") or "").lower()
            if run_state in terminal_states:
                return
            if time.monotonic() >= deadline:
                raise TimeoutError(f"Schemathesis run timed out: {run_id}")
            if interval > 0:
                time.sleep(interval)

    def _stage_result_from_payload(
        self,
        *,
        stage: OperationTestStage,
        run_id: str,
        payload: dict[str, Any],
    ) -> OperationTestStageResult:
        failure_ids = [str(item) for item in payload.get("failure_ids", [])]
        outcome = str(payload.get("outcome") or payload.get("status") or ("failed" if failure_ids else "passed"))
        status = "passed" if outcome.lower() in {"passed", "success", "succeeded"} and not failure_ids else "failed"
        artifact_refs = _artifact_refs(payload.get("artifacts"))
        findings: list[OperationTestFinding] = []
        if status == "failed":
            findings.append(
                OperationTestFinding(
                    stage=stage.name,
                    severity="high",
                    title="Schemathesis reported operation failures",
                    summary=f"Schemathesis found {len(failure_ids) or 1} failure(s) in {stage.name}.",
                    evidence_refs=failure_ids or [run_id],
                    artifact_refs=artifact_refs,
                )
            )

        summary = payload.get("summary")
        return OperationTestStageResult(
            stage=stage.name,
            status=status,
            run_id=run_id,
            outcome=outcome,
            summary=summary if isinstance(summary, dict) else {},
            failure_ids=failure_ids,
            artifact_refs=artifact_refs,
            findings=findings,
        )


def _artifact_refs(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        return [{"name": str(name), "uri": str(uri)} for name, uri in value.items()]
    if isinstance(value, list):
        refs: list[dict[str, Any]] = []
        for item in value:
            if isinstance(item, dict):
                refs.append(item)
            else:
                refs.append({"uri": str(item)})
        return refs
    if value is None:
        return []
    return [{"uri": str(value)}]
