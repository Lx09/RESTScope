from __future__ import annotations


def _tool_spec(name: str, *, read_only: bool, requires_approval: bool, risk_level: str):
    from restscope.llm import ToolSpec

    return ToolSpec(
        name=name,
        description=f"{name} description",
        kind="mcp_tool",
        input_schema={"type": "object"},
        read_only=read_only,
        requires_approval=requires_approval,
        risk_level=risk_level,
    )


def _bind_context(executor, *, base_url=None, headers=None, schema_source=None) -> None:
    from restscope.capabilities import ToolContext
    from restscope.openapi_parser import OpenAPIParser

    ir = OpenAPIParser.parse(
        {
            "openapi": "3.0.3",
            "info": {"title": "Runner", "version": "1.0"},
            "paths": {"/pets": {"get": {"responses": {"200": {"description": "ok"}}}}},
        }
    )
    executor.bind_context(
        ToolContext(
            ir=ir,
            baseline_schema_source=schema_source or {"kind": "file", "path": "api.yaml"},
            base_url=base_url,
            headers=headers or {},
        )
    )


def test_operation_tester_can_start_run_without_state_permission() -> None:
    from restscope.capabilities import ToolPolicy

    policy = ToolPolicy()
    start_run = _tool_spec(
        "mcp.schemathesis.start_run",
        read_only=False,
        requires_approval=True,
        risk_level="high",
    )
    cancel_run = _tool_spec(
        "mcp.schemathesis.cancel_run",
        read_only=False,
        requires_approval=True,
        risk_level="medium",
    )
    get_result = _tool_spec(
        "mcp.schemathesis.get_result",
        read_only=True,
        requires_approval=False,
        risk_level="low",
    )

    assert policy.is_allowed(role="operation_tester", tool_spec=start_run, state={}) is True
    assert policy.is_allowed(
        role="operation_tester",
        tool_spec=cancel_run,
        state={},
    ) is False
    assert policy.is_allowed(role="operation_tester", tool_spec=get_result, state={}) is True


def test_tool_validator_does_not_require_state_permission_for_operation_test_run() -> None:
    from restscope.capabilities import ToolCallValidator, ToolPolicy, ToolRegistry
    from restscope.llm import ToolCall

    registry = ToolRegistry()
    registry.register(
        spec=_tool_spec(
            "mcp.schemathesis.start_run",
            read_only=False,
            requires_approval=True,
            risk_level="high",
        )
    )
    validator = ToolCallValidator(registry, ToolPolicy())

    allowed = validator.validate(
        tool_call=ToolCall(id="call_1", name="mcp.schemathesis.start_run", arguments={}),
        role="operation_tester",
        state={},
    )

    assert allowed == []


def test_schemathesis_runner_uses_tool_executor_and_operation_filter() -> None:
    from restscope.agent import (
        OperationReference,
        OperationTarget,
        SchemathesisOperationRunner,
    )
    from restscope.capabilities import ToolCallValidator, ToolExecutor, ToolPolicy, ToolRegistry

    calls: list[tuple[str, dict]] = []
    registry = ToolRegistry()
    for name, read_only, requires_approval, risk_level in [
        ("mcp.schemathesis.start_run", False, True, "high"),
        ("mcp.schemathesis.get_run", True, False, "low"),
        ("mcp.schemathesis.get_result", True, False, "low"),
        ("mcp.schemathesis.get_failure", True, False, "low"),
    ]:
        registry.register(
            spec=_tool_spec(
                name,
                read_only=read_only,
                requires_approval=requires_approval,
                risk_level=risk_level,
            ),
            handler=lambda _context, /, _name=name, **arguments: _handle_tool_call(calls, _name, arguments),
        )
    executor = ToolExecutor(registry, ToolCallValidator(registry, ToolPolicy()))
    _bind_context(
        executor,
        base_url="http://localhost:8000",
        schema_source={"kind": "file", "path": "assets/openapi/petstore-v3.json"},
    )
    runner = SchemathesisOperationRunner(tool_executor=executor, poll_interval=0, poll_timeout=1)

    result = runner.run_operation(
        target=OperationTarget(
            operation=OperationReference(method="get", path="/pets"),
        ),
        state={},
    )

    assert result.outcome == "passed"
    assert calls[0][0] == "mcp.schemathesis.start_run"
    assert calls[0][1]["include"] == {"path": "/pets", "method": "GET"}
    assert calls[0][1]["schema"] == {"kind": "file", "path": "assets/openapi/petstore-v3.json"}
    assert set(calls[0][1]) == {"schema", "base_url", "include"}
    assert result.status_code_counts == {"200": 3}


def test_schemathesis_runner_reads_at_most_twenty_compact_failure_summaries() -> None:
    from restscope.agent import OperationReference, OperationTarget, SchemathesisOperationRunner
    from restscope.capabilities import ToolCallValidator, ToolExecutor, ToolPolicy, ToolRegistry

    calls: list[tuple[str, dict]] = []
    registry = ToolRegistry()
    for name, read_only, requires_approval, risk_level in [
        ("mcp.schemathesis.start_run", False, True, "high"),
        ("mcp.schemathesis.get_run", True, False, "low"),
        ("mcp.schemathesis.get_result", True, False, "low"),
        ("mcp.schemathesis.get_failure", True, False, "low"),
    ]:
        registry.register(
            spec=_tool_spec(name, read_only=read_only, requires_approval=requires_approval, risk_level=risk_level),
            handler=lambda _context, /, _name=name, **arguments: _handle_failure_tool_call(calls, _name, arguments),
        )
    executor = ToolExecutor(registry, ToolCallValidator(registry, ToolPolicy()))
    _bind_context(executor, headers={"Authorization": "Bearer runtime-secret"})
    runner = SchemathesisOperationRunner(
        tool_executor=executor,
        poll_interval=0,
        poll_timeout=1,
    )

    result = runner.run_operation(
        target=OperationTarget(
            operation=OperationReference(method="GET", path="/pets"),
        ),
        state={},
    )

    assert len(result.failure_ids) == 25
    assert len(result.failure_summaries) == 20
    assert sum(name == "mcp.schemathesis.get_failure" for name, _ in calls) == 20
    assert result.failure_summaries[0].response_status == 404
    assert "runtime-secret" not in result.model_dump_json()
    assert "response-secret" not in result.model_dump_json()


def _handle_tool_call(calls: list[tuple[str, dict]], name: str, arguments: dict) -> dict:
    calls.append((name, arguments))
    if name == "mcp.schemathesis.start_run":
        return {"structured": {"run_id": "run_1", "state": "queued"}}
    if name == "mcp.schemathesis.get_run":
        return {"structured": {"run_id": arguments["run_id"], "state": "completed"}}
    if name == "mcp.schemathesis.get_result":
        return {
            "structured": {
                "run_id": arguments["run_id"],
                "outcome": "passed",
                "summary": {"checks": {"passed": 3}, "status_code_counts": {"200": 3}},
                "failure_ids": [],
                "artifacts": {"events": "schemathesis://runs/run_1/events.ndjson"},
            }
        }
    raise AssertionError(name)


def _handle_failure_tool_call(calls: list[tuple[str, dict]], name: str, arguments: dict) -> dict:
    calls.append((name, arguments))
    if name == "mcp.schemathesis.start_run":
        assert set(arguments) == {"schema", "headers", "include"}
        return {"structured": {"run_id": "run_failures"}}
    if name == "mcp.schemathesis.get_run":
        return {"structured": {"state": "completed"}}
    if name == "mcp.schemathesis.get_result":
        return {
            "structured": {
                "outcome": "failed",
                "summary": {"status_code_counts": {"404": 25}},
                "failure_ids": [f"failure_{index}" for index in range(25)],
            }
        }
    if name == "mcp.schemathesis.get_failure":
        return {
            "structured": {
                "failure_id": arguments["failure_id"],
                "check": "status_code_conformance",
                "title": "Unexpected status",
                "message": "Expected a documented status",
                "request": {"headers": {"Authorization": "response-secret"}, "body": "response-secret"},
                "response": {"status_code": 404, "body": "response-secret"},
                "curl": "curl -H response-secret",
            }
        }
    raise AssertionError(name)
