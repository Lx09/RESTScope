"""Regression scenarios for App context and explicit tool dependencies."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest


def _context(*, secret: str = "Bearer runtime-secret"):
    """Build one immutable App target context for boundary tests."""
    from restscope.tools import ToolContext
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


def test_harness_runtime_binds_context_once_and_exposes_exact_operations() -> None:
    """The App owns context lifecycle without an executable global registry."""
    from restscope.harness import build_harness
    from restscope.tools import ToolContextError

    runtime = build_harness()
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


def test_harness_runtime_injects_monitor_catalogs_without_registering_tools(
    tmp_path: Path,
) -> None:
    """The runtime exposes lookup implementations but owns no global toolbox."""
    from restscope.api_behavior_monitor.resource_catalog import ResourceCatalog
    from restscope.api_behavior_monitor.response_value_catalog import (
        ResponseValueCatalog,
    )
    from restscope.harness import build_harness
    from restscope.tools import ResourceIdentifierCapability
    from restscope.db import (
        Base,
        SqlAlchemyResourceCatalogUnitOfWork,
        SqlAlchemyResponseValueCatalogUnitOfWork,
        create_engine_from_url,
        make_session_factory,
    )

    engine = create_engine_from_url(f"sqlite:///{tmp_path / 'catalogs.sqlite'}")
    Base.metadata.create_all(engine)
    session_factory = make_session_factory(engine)
    resource_catalog = ResourceCatalog(
        lambda: SqlAlchemyResourceCatalogUnitOfWork(session_factory)
    )
    response_catalog = ResponseValueCatalog(
        lambda: SqlAlchemyResponseValueCatalogUnitOfWork(session_factory)
    )
    monitor = SimpleNamespace(
        resource_identifier_tracker=SimpleNamespace(catalog=resource_catalog),
        response_value_tracker=SimpleNamespace(catalog=response_catalog),
    )

    runtime = build_harness(api_behavior_monitor_coordinator=monitor)
    runtime.bind_context(_context())

    assert isinstance(
        runtime.resource_identifier_capability,
        ResourceIdentifierCapability,
    )
    assert runtime.resource_identifier_capability.list_resources()["structured"] == {
        "resources": [],
        "total": 0,
        "offset": 0,
    }
    assert runtime.openapi_capability.find_observed_response_fields(
        name="pet_id"
    )["structured"] == {
        "requested_name": "pet_id",
        "responses": [],
        "total": 0,
        "offset": 0,
    }
    assert runtime.external_tools is None


def test_agent_tool_binds_context_explicitly_and_cannot_be_replaced_by_arguments() -> None:
    """Only the implementation closure chooses whether it needs App context."""
    from restscope.tools import AgentToolbox
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
    from restscope.harness import build_harness
    from restscope.tools import AgentToolbox, ToolContextError
    from restscope.llm import ToolCall, ToolSpec

    runtime = build_harness()
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
