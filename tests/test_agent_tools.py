"""Public behavior scenarios for one Agent's explicitly registered tools."""

from __future__ import annotations

import pytest


def test_agent_toolbox_rejects_duplicate_tool_names() -> None:
    """Scenario: a second registration cannot replace an existing tool."""
    from restscope.capabilities import AgentToolbox
    from restscope.llm import ToolSpec

    toolbox = AgentToolbox()
    spec = ToolSpec(
        name="catalog.lookup",
        description="Read one catalog entry.",
        kind="local_function",
        input_schema={"type": "object"},
        output_schema={"type": "object"},
    )
    toolbox.register(spec=spec, execute=lambda **arguments: arguments)

    with pytest.raises(ValueError, match="already registered"):
        toolbox.register(spec=spec, execute=lambda **arguments: arguments)


def test_agent_toolbox_rejects_a_missing_tool_implementation() -> None:
    """Scenario: a model-visible tool can never be registered half-built."""
    from restscope.capabilities import AgentToolbox
    from restscope.llm import ToolSpec

    toolbox = AgentToolbox()
    spec = ToolSpec(
        name="catalog.lookup",
        description="Read one catalog entry.",
        kind="local_function",
        input_schema={"type": "object"},
        output_schema={"type": "object"},
    )

    with pytest.raises(TypeError, match="executable"):
        toolbox.register(spec=spec, execute=None)  # type: ignore[arg-type]


def test_agent_toolbox_rejects_invalid_arguments_before_execution() -> None:
    """Scenario: malformed model arguments never reach the tool code."""
    from restscope.capabilities import AgentToolbox
    from restscope.llm import ToolCall, ToolSpec

    executed: list[dict] = []
    toolbox = AgentToolbox()
    toolbox.register(
        spec=ToolSpec(
            name="catalog.lookup",
            description="Read one catalog entry.",
            kind="local_function",
            input_schema={
                "type": "object",
                "properties": {"case_id": {"type": "string", "minLength": 1}},
                "required": ["case_id"],
                "additionalProperties": False,
            },
            output_schema={"type": "object"},
        ),
        execute=lambda **arguments: executed.append(arguments) or {},
    )

    result = toolbox.execute(
        ToolCall(
            id="invalid-arguments",
            name="catalog.lookup",
            arguments={"case_id": "", "unexpected": True},
        )
    )

    assert result.status == "denied"
    assert result.error == {
        "code": "invalid_tool_arguments",
        "message": "Tool arguments do not match the declared input schema.",
    }
    assert executed == []


def test_agent_toolbox_rejects_success_output_that_breaks_its_schema() -> None:
    """Scenario: malformed implementation output never reaches the Agent."""
    from restscope.capabilities import AgentToolbox
    from restscope.llm import ToolCall, ToolSpec

    toolbox = AgentToolbox()
    toolbox.register(
        spec=ToolSpec(
            name="catalog.count",
            description="Count retained cases.",
            kind="local_function",
            input_schema={"type": "object", "additionalProperties": False},
            output_schema={
                "type": "object",
                "properties": {"count": {"type": "integer"}},
                "required": ["count"],
                "additionalProperties": False,
            },
        ),
        execute=lambda: {"structured": {"count": "not-an-integer"}},
    )

    result = toolbox.execute(
        ToolCall(id="invalid-output", name="catalog.count", arguments={})
    )

    assert result.status == "failed"
    assert result.structured is None
    assert result.error == {
        "code": "invalid_tool_output",
        "message": "Tool output does not match the declared output schema.",
    }


def test_agent_toolbox_hides_unexpected_exception_details() -> None:
    """Scenario: an implementation defect cannot leak secrets to the model."""
    from restscope.capabilities import AgentToolbox
    from restscope.llm import ToolCall, ToolSpec

    def fail() -> dict:
        raise RuntimeError("database failed with secret-password")

    toolbox = AgentToolbox()
    toolbox.register(
        spec=ToolSpec(
            name="catalog.fail",
            description="Exercise a failed lookup.",
            kind="local_function",
            input_schema={"type": "object", "additionalProperties": False},
            output_schema={"type": "object"},
        ),
        execute=fail,
    )

    result = toolbox.execute(
        ToolCall(id="unexpected-error", name="catalog.fail", arguments={})
    )

    assert result.status == "failed"
    assert result.error == {
        "code": "internal_tool_error",
        "message": "The tool failed because of an internal error.",
    }
    assert "secret-password" not in result.model_dump_json()


def test_agent_toolbox_returns_only_its_registered_specs_in_order() -> None:
    """Scenario: an Agent offers exactly the tools registered for that Agent."""
    from restscope.capabilities import AgentToolbox
    from restscope.llm import ToolSpec

    toolbox = AgentToolbox()
    first = ToolSpec(
        name="operation.inputs",
        description="Read current operation inputs.",
        kind="local_function",
        input_schema={"type": "object"},
        output_schema={"type": "object"},
    )
    second = ToolSpec(
        name="catalog.query",
        description="Read current test cases.",
        kind="local_function",
        input_schema={"type": "object"},
        output_schema={"type": "object"},
    )
    toolbox.register(spec=first, execute=lambda: {"structured": {}})
    toolbox.register(spec=second, execute=lambda: {"structured": {}})

    assert toolbox.specs() == [first, second]


def test_agent_toolbox_executes_independent_calls_concurrently_in_call_order() -> None:
    """Scenario: parallel completion never changes provider result ordering."""
    import threading
    import time

    from restscope.capabilities import AgentToolbox
    from restscope.llm import ToolCall, ToolSpec

    barrier = threading.Barrier(2, timeout=1)
    completion_order: list[str] = []

    def query(*, value: str) -> dict:
        barrier.wait()
        if value == "first":
            time.sleep(0.02)
        completion_order.append(value)
        return {"structured": {"value": value}}

    toolbox = AgentToolbox()
    toolbox.register(
        spec=ToolSpec(
            name="catalog.query",
            description="Read one independent fact.",
            kind="local_function",
            input_schema={
                "type": "object",
                "properties": {"value": {"type": "string"}},
                "required": ["value"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {"value": {"type": "string"}},
                "required": ["value"],
                "additionalProperties": False,
            },
        ),
        execute=query,
    )
    calls = [
        ToolCall(id="first", name="catalog.query", arguments={"value": "first"}),
        ToolCall(id="second", name="catalog.query", arguments={"value": "second"}),
    ]

    results = toolbox.execute_many(calls)

    assert completion_order == ["second", "first"]
    assert [result.tool_call_id for result in results] == ["first", "second"]
    assert [result.structured for result in results] == [
        {"value": "first"},
        {"value": "second"},
    ]


def test_agent_toolbox_validates_a_whole_batch_before_any_call_runs() -> None:
    """Scenario: one invalid call prevents every implementation side effect."""
    from restscope.capabilities import AgentToolbox
    from restscope.llm import ToolCall, ToolSpec

    executed: list[str] = []
    toolbox = AgentToolbox()
    toolbox.register(
        spec=ToolSpec(
            name="catalog.query",
            description="Read one independent fact.",
            kind="local_function",
            input_schema={
                "type": "object",
                "properties": {"value": {"type": "string"}},
                "required": ["value"],
                "additionalProperties": False,
            },
            output_schema={"type": "object"},
        ),
        execute=lambda **arguments: executed.append(arguments["value"]) or {
            "structured": arguments
        },
    )

    results = toolbox.execute_many(
        [
            ToolCall(
                id="valid",
                name="catalog.query",
                arguments={"value": "would-run"},
            ),
            ToolCall(
                id="invalid",
                name="catalog.query",
                arguments={"unexpected": True},
            ),
        ]
    )

    assert executed == []
    assert [result.status for result in results] == ["denied", "denied"]
    assert [result.error["code"] for result in results if result.error] == [
        "tool_batch_rejected",
        "invalid_tool_arguments",
    ]


def test_agent_toolbox_requires_output_schema_for_restscope_tools() -> None:
    """Scenario: an owned tool cannot opt out of its success contract."""
    from restscope.capabilities import AgentToolbox
    from restscope.llm import ToolSpec

    toolbox = AgentToolbox()
    spec = ToolSpec(
        name="catalog.query",
        description="Read one catalog fact.",
        kind="local_function",
        input_schema={"type": "object"},
        output_schema=None,
    )

    with pytest.raises(ValueError, match="output schema"):
        toolbox.register(spec=spec, execute=lambda: {"structured": {}})


def test_agent_toolbox_rejects_invalid_json_schemas_during_registration() -> None:
    """Scenario: a broken contract fails at startup, not during an Agent run."""
    from restscope.capabilities import AgentToolbox
    from restscope.llm import ToolSpec

    toolbox = AgentToolbox()
    spec = ToolSpec(
        name="catalog.query",
        description="Read one catalog fact.",
        kind="local_function",
        input_schema={"type": "not-a-json-schema-type"},
        output_schema={"type": "object"},
    )

    with pytest.raises(ValueError, match="invalid input schema"):
        toolbox.register(spec=spec, execute=lambda: {"structured": {}})


def test_agent_toolbox_redacts_every_model_visible_success_value() -> None:
    """Scenario: the App redactor is the final model-result boundary."""
    from restscope.capabilities import AgentToolbox
    from restscope.llm import ToolCall, ToolSpec
    from restscope.observability import TracingRuntime
    from restscope.redaction import Redactor

    secret = "tool-secret"
    toolbox = AgentToolbox(
        tracing_runtime=TracingRuntime.disabled(redactor=Redactor([secret]))
    )
    toolbox.register(
        spec=ToolSpec(
            name="catalog.query",
            description="Read one catalog fact.",
            kind="local_function",
            input_schema={"type": "object", "additionalProperties": False},
            output_schema={
                "type": "object",
                "properties": {"value": {"type": "string"}},
                "required": ["value"],
            },
        ),
        execute=lambda: {
            "content": f"value={secret}",
            "structured": {"value": secret},
        },
    )

    result = toolbox.execute(
        ToolCall(id="redacted", name="catalog.query", arguments={})
    )

    assert result.status == "succeeded"
    assert result.content == "value=***REDACTED***"
    assert result.structured == {"value": "***REDACTED***"}
    assert secret not in result.model_dump_json()


def test_agent_toolbox_returns_an_explicit_expected_failure() -> None:
    """Scenario: a domain rejection reaches the model with its safe contract."""
    from restscope.capabilities import AgentToolbox, ToolFailure
    from restscope.llm import ToolCall, ToolSpec

    def reject() -> dict:
        raise ToolFailure(
            code="catalog_value_missing",
            message="The requested Catalog value is unavailable.",
            content="CATALOG VALUE UNAVAILABLE",
        )

    toolbox = AgentToolbox()
    toolbox.register(
        spec=ToolSpec(
            name="catalog.query",
            description="Read one catalog fact.",
            kind="local_function",
            input_schema={"type": "object"},
            output_schema={"type": "object"},
        ),
        execute=reject,
    )

    result = toolbox.execute(
        ToolCall(id="expected-failure", name="catalog.query", arguments={})
    )

    assert result.status == "failed"
    assert result.content == "CATALOG VALUE UNAVAILABLE"
    assert result.error == {
        "code": "catalog_value_missing",
        "message": "The requested Catalog value is unavailable.",
    }
