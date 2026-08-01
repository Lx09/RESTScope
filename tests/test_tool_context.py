"""Regression scenarios for App context and explicit tool dependencies."""

from __future__ import annotations

import pytest


def _context(*, secret: str = "Bearer runtime-secret"):
    """Build one immutable App target context for boundary tests."""
    from restscope.capabilities import ToolContext
    from restscope.openapi_parser import OpenAPIParser

    ir = OpenAPIParser.parse(
        {
            "openapi": "3.0.3",
            "info": {"title": "Context API", "version": "1.0"},
            "paths": {
                "/pets": {
                    "get": {
                        "operationId": "listPets",
                        "responses": {"200": {"description": "ok"}},
                    }
                }
            },
        }
    )
    return ToolContext(
        ir=ir,
        baseline_schema_source={"kind": "file", "path": "api.yaml"},
        base_url="https://api.example.test",
        headers={"Authorization": secret},
    )


def test_capability_runtime_binds_context_once_and_exposes_exact_operations() -> None:
    """The App owns context lifecycle without an executable global registry."""
    from restscope.capabilities import ToolContextError, build_capabilities

    runtime = build_capabilities()
    context = _context()
    runtime.bind_context(context)

    assert runtime.require_context() is context
    assert runtime.require_operation("GET /pets").operation_key == "GET /pets"
    assert runtime.openapi_capability.list_inputs(
        operation_key="GET /pets"
    )["structured"] == {
        "operation_key": "GET /pets",
        "inputs": [],
        "total": 0,
        "offset": 0,
    }
    with pytest.raises(ToolContextError) as exc_info:
        runtime.bind_context(context)
    assert exc_info.value.code == "tool_context_already_initialized"


def test_agent_tool_binds_context_explicitly_and_cannot_be_replaced_by_arguments() -> None:
    """Only the implementation closure chooses whether it needs App context."""
    from restscope.capabilities import AgentToolbox
    from restscope.llm import ToolCall, ToolSpec

    context = _context()
    seen = []
    toolbox = AgentToolbox()
    spec = ToolSpec(
        name="context.inspect",
        description="Inspect explicitly bound context",
        kind="local_function",
        input_schema={"type": "object", "additionalProperties": True},
        output_schema={"type": "object"},
    )

    def inspect(**arguments):
        """Use the captured context while treating same-named input as data."""
        seen.append((context, arguments))
        return {"structured": {"title": context.ir.meta.title}}

    toolbox.register(spec=spec, execute=inspect)
    result = toolbox.execute(
        ToolCall(
            id="override",
            name=spec.name,
            arguments={"context": {"headers": {"Authorization": "attacker"}}},
        )
    )

    assert result.status == "succeeded"
    assert seen[0][0] is context
    assert seen[0][1]["context"] != context
    assert result.structured == {"title": "Context API"}


def test_missing_context_is_stable_and_unknown_tool_errors_hide_headers() -> None:
    """Lifecycle errors are explicit while implementation details stay internal."""
    from restscope.capabilities import AgentToolbox, ToolContextError, build_capabilities
    from restscope.llm import ToolCall, ToolSpec

    runtime = build_capabilities()
    with pytest.raises(ToolContextError) as exc_info:
        runtime.require_context()
    assert exc_info.value.code == "tool_context_not_initialized"

    runtime.bind_context(_context())
    toolbox = AgentToolbox()
    spec = ToolSpec(
        name="context.fail",
        description="Exercise the unknown-error boundary",
        kind="local_function",
        input_schema={"type": "object", "properties": {}},
        output_schema={"type": "object"},
    )

    def fail():
        """Raise an unexpected error containing target authentication."""
        raise RuntimeError(
            f"request failed with {runtime.require_context().headers['Authorization']}"
        )

    toolbox.register(spec=spec, execute=fail)
    result = toolbox.execute(ToolCall(id="fail", name=spec.name, arguments={}))

    assert result.status == "failed"
    assert result.error == {
        "code": "internal_tool_error",
        "message": "The tool failed because of an internal error.",
    }
    assert "runtime-secret" not in result.model_dump_json()
