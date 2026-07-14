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


def test_operation_tester_requires_live_testing_permission_for_start_run() -> None:
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

    assert policy.is_allowed(role="operation_tester", tool_spec=start_run, state={}) is False
    assert policy.is_allowed(
        role="operation_tester",
        tool_spec=start_run,
        state={"allow_live_testing": True},
    ) is True
    assert policy.is_allowed(
        role="operation_tester",
        tool_spec=cancel_run,
        state={"allow_live_testing": True},
    ) is False
    assert policy.is_allowed(role="operation_tester", tool_spec=get_result, state={}) is True


def test_tool_validator_does_not_require_approval_for_authorized_operation_test_run() -> None:
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

    denied = validator.validate(
        tool_call=ToolCall(id="call_1", name="mcp.schemathesis.start_run", arguments={}),
        role="operation_tester",
        state={},
    )
    allowed = validator.validate(
        tool_call=ToolCall(id="call_2", name="mcp.schemathesis.start_run", arguments={}),
        role="operation_tester",
        state={"allow_live_testing": True},
    )

    assert {error["type"] for error in denied} == {"tool_not_allowed", "approval_required"}
    assert allowed == []


def test_schemathesis_runner_uses_tool_executor_and_operation_filter() -> None:
    from restscope.agent import (
        OperationTarget,
        SchemathesisOperationRunner,
        StageOptions,
        default_operation_test_stages,
    )
    from restscope.capabilities import ToolCallValidator, ToolExecutor, ToolPolicy, ToolRegistry

    calls: list[tuple[str, dict]] = []
    registry = ToolRegistry()
    for name, read_only, requires_approval, risk_level in [
        ("mcp.schemathesis.start_run", False, True, "high"),
        ("mcp.schemathesis.get_run", True, False, "low"),
        ("mcp.schemathesis.get_result", True, False, "low"),
    ]:
        registry.register(
            spec=_tool_spec(
                name,
                read_only=read_only,
                requires_approval=requires_approval,
                risk_level=risk_level,
            ),
            handler=lambda _name=name, **arguments: _handle_tool_call(calls, _name, arguments),
        )
    executor = ToolExecutor(registry, ToolCallValidator(registry, ToolPolicy()))
    runner = SchemathesisOperationRunner(tool_executor=executor, poll_interval=0, poll_timeout=1)

    result = runner.run_stage(
        stage=default_operation_test_stages()[0],
        target=OperationTarget(
            schema_source={"kind": "file", "path": "assets/openapi/petstore-v3.json"},
            base_url="http://localhost:8000",
            method="get",
            path="/pets",
            headers={},
        ),
        options=StageOptions(max_examples=5, max_failures=2, max_time=30, seed=7),
        state={"allow_live_testing": True},
    )

    assert result.status == "passed"
    assert calls[0][0] == "mcp.schemathesis.start_run"
    assert calls[0][1]["include"] == {"path": "/pets", "method": "GET"}
    assert calls[0][1]["schema"] == {"kind": "file", "path": "assets/openapi/petstore-v3.json"}


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
                "summary": {"checks": {"passed": 3}},
                "failure_ids": [],
                "artifacts": {"events": "schemathesis://runs/run_1/events.ndjson"},
            }
        }
    raise AssertionError(name)
