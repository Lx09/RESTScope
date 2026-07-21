from __future__ import annotations

import pytest


def _context(*, secret: str = "Bearer runtime-secret"):
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


def test_tool_executor_binds_context_once_and_injects_it_out_of_band() -> None:
    from restscope.capabilities import ToolCallValidator, ToolContextError, ToolExecutor, ToolPolicy, ToolRegistry
    from restscope.llm import ToolCall, ToolSpec

    seen = []
    registry = ToolRegistry()
    spec = ToolSpec(
        name="context.inspect",
        description="Inspect bound context",
        kind="local_function",
        input_schema={
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
        },
    )

    def handler(context, /, *, value):
        seen.append(context)
        return {"structured": {"value": value, "title": context.ir.meta.title}}

    registry.register(spec=spec, handler=handler)
    executor = ToolExecutor(registry, ToolCallValidator(registry, ToolPolicy()))
    context = _context()
    executor.bind_context(context)

    result = executor.execute(
        tool_call=ToolCall(id="inspect", name=spec.name, arguments={"value": "ok"}),
        role="decision_maker",
        state={},
    )

    assert seen == [context]
    assert result.structured == {"value": "ok", "title": "Context API"}
    assert "context" not in spec.input_schema["properties"]
    assert "runtime-secret" not in result.model_dump_json()
    with pytest.raises(ToolContextError) as exc_info:
        executor.bind_context(context)
    assert exc_info.value.code == "tool_context_already_initialized"


def test_model_arguments_cannot_replace_the_bound_context() -> None:
    from restscope.capabilities import ToolCallValidator, ToolExecutor, ToolPolicy, ToolRegistry
    from restscope.llm import ToolCall, ToolSpec

    seen = []
    registry = ToolRegistry()
    spec = ToolSpec(
        name="context.override_attempt",
        description="Observe an untrusted argument with a reserved-looking name",
        kind="local_function",
        input_schema={"type": "object", "additionalProperties": True},
    )

    def handler(context, /, **arguments):
        seen.append((context, arguments))
        return {"structured": {"title": context.ir.meta.title}}

    registry.register(spec=spec, handler=handler)
    executor = ToolExecutor(registry, ToolCallValidator(registry, ToolPolicy()))
    context = _context()
    executor.bind_context(context)

    result = executor.execute(
        tool_call=ToolCall(
            id="override",
            name=spec.name,
            arguments={"context": {"headers": {"Authorization": "attacker"}}},
        ),
        role="decision_maker",
        state={},
    )

    assert result.status == "succeeded"
    assert seen[0][0] is context
    assert seen[0][1]["context"] != context
    assert result.structured == {"title": "Context API"}


def test_tool_executor_requires_context_and_redacts_bound_header_values() -> None:
    from restscope.capabilities import ToolCallValidator, ToolContextError, ToolExecutor, ToolPolicy, ToolRegistry
    from restscope.llm import ToolCall, ToolSpec

    registry = ToolRegistry()
    spec = ToolSpec(
        name="context.fail",
        description="Fail with a secret",
        kind="local_function",
        input_schema={"type": "object", "properties": {}},
    )

    def handler(context, /):
        raise RuntimeError(f"request failed with {context.headers['Authorization']}")

    registry.register(spec=spec, handler=handler)
    executor = ToolExecutor(registry, ToolCallValidator(registry, ToolPolicy()))

    with pytest.raises(ToolContextError) as exc_info:
        executor.require_context()
    assert exc_info.value.code == "tool_context_not_initialized"

    executor.bind_context(_context())
    result = executor.execute(
        tool_call=ToolCall(id="fail", name=spec.name, arguments={}),
        role="decision_maker",
        state={},
    )

    assert result.status == "failed"
    assert result.error is not None
    assert result.error["message"] == "request failed with ***REDACTED***"
    assert "runtime-secret" not in result.model_dump_json()
