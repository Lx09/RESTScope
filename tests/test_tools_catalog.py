"""Protect the global, subject-grouped Tool Catalog and its schema contract."""

from __future__ import annotations

import pytest
from jsonschema import ValidationError, validate

from restscope.llm import ToolSpec

EXPECTED_BUILTIN_TOOLS = {
    "database.query": "database",
    "restscope.http.request": "http",
    "openapi.list_inputs": "openapi",
    "openapi.list_response_fields": "openapi",
    "openapi.find_observed_response_fields": "openapi",
    "openapi.get_input_schema": "openapi",
    "openapi.get_response_field_schema": "openapi",
    "openapi.list_operations": "openapi",
    "resource.list_resources": "resource",
    "resource.list_ids": "resource",
    "test_case.run_batch": "test_case",
    "test_case.get_batch_results": "test_case",
    "test_case.get": "test_case",
    "request_generation.get_input_state": "request_generation",
    "request_generation.validate_patch": "request_generation",
    "parameter_patch.apply": "parameter_patch",
    "plan.read": "plan",
    "plan.update": "plan",
    "skill.read": "skill",
    "file.read": "file",
    "subagent.start": "subagent",
    "subagent.wait": "subagent",
    "subagent.cancel": "subagent",
}


def test_builtin_catalog_contains_every_owned_tool_by_subject() -> None:
    """Scenario: callers discover every RESTScope Tool from one Interface."""
    from restscope.tools import builtin_tool_catalog

    catalog = builtin_tool_catalog()

    assert {
        definition.name: definition.subject
        for definition in catalog.definitions()
    } == EXPECTED_BUILTIN_TOOLS


def test_catalog_rejects_invalid_or_duplicate_definitions() -> None:
    """Scenario: a bad Tool contract fails while the Harness is constructed."""
    from restscope.tools import ToolCatalog, ToolDefinition

    valid = ToolDefinition(
        subject="test_case",
        spec=ToolSpec(
            name="test_case.inspect",
            description="Inspect one Test Case.",
            kind="local_function",
            input_schema={
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        ),
    )
    with pytest.raises(ValueError, match="already defined"):
        ToolCatalog([valid, valid])

    invalid = valid.model_copy(
        update={
            "spec": valid.spec.model_copy(
                update={"input_schema": {"type": "not-a-json-schema-type"}}
            )
        }
    )
    with pytest.raises(ValueError, match="invalid input schema"):
        ToolCatalog([invalid])


def test_local_tool_requires_a_complete_output_schema() -> None:
    """Scenario: a RESTScope Tool cannot hide its successful result shape."""
    from restscope.tools import ToolDefinition

    with pytest.raises(ValueError, match="output schema"):
        ToolDefinition(
            subject="test_case",
            spec=ToolSpec(
                name="test_case.incomplete",
                description="Incomplete example.",
                kind="local_function",
                input_schema={
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
            ),
        )


def test_local_tool_requires_closed_object_boundaries() -> None:
    """Scenario: Catalog construction rejects vague local input/output envelopes."""
    from restscope.tools import ToolDefinition

    with pytest.raises(ValueError, match="input must reject additional properties"):
        ToolDefinition(
            subject="test_case",
            spec=ToolSpec(
                name="test_case.vague",
                description="Vague example.",
                kind="local_function",
                input_schema={"type": "object"},
                output_schema={
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
            ),
        )


def test_toolbox_binds_exactly_the_catalog_definitions_selected_for_an_agent() -> None:
    """Scenario: a live implementation cannot drift from Profile permissions."""
    from restscope.tools import AgentToolbox, ToolBinding, builtin_tool_catalog

    toolbox = AgentToolbox.from_catalog(
        catalog=builtin_tool_catalog(),
        selected_names=("openapi.list_inputs",),
        bindings=[
            ToolBinding(
                name="openapi.list_inputs",
                execute=lambda **_arguments: {
                    "structured": {"inputs": [], "total": 0, "offset": 0}
                },
            )
        ],
    )

    assert [spec.name for spec in toolbox.specs()] == ["openapi.list_inputs"]
    with pytest.raises(ValueError, match="do not match"):
        AgentToolbox.from_catalog(
            catalog=builtin_tool_catalog(),
            selected_names=("openapi.list_inputs",),
            bindings=[],
        )

    with pytest.raises(TypeError, match="unexpected keyword argument 'spec'"):
        ToolBinding(
            name="openapi.list_inputs",
            execute=lambda **_arguments: {},
            spec=builtin_tool_catalog().get("openapi.list_inputs").spec,
        )


def test_builtin_schemas_do_not_hide_unexplained_open_values() -> None:
    """Scenario: intentionally arbitrary JSON is visible in its field description."""
    from restscope.tools import builtin_tool_catalog

    unexplained: list[str] = []

    def inspect(value, *, path: str, parent_key: str | None = None) -> None:
        """Find silent open values while allowing documented target-shaped JSON."""
        if isinstance(value, dict):
            if not value and parent_key != "properties":
                unexplained.append(path)
            if value.get("type") == "object" and value.get(
                "additionalProperties"
            ) is True and not value.get("description"):
                unexplained.append(f"{path}.description")
            for key, child in value.items():
                inspect(child, path=f"{path}.{key}", parent_key=key)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                inspect(child, path=f"{path}[{index}]")

    for definition in builtin_tool_catalog().definitions():
        assert definition.spec.description.strip()
        assert definition.spec.input_schema.get("type") == "object"
        assert definition.spec.input_schema.get("additionalProperties") is False
        inspect(
            definition.spec.input_schema,
            path=f"{definition.name}.input",
        )
        inspect(
            definition.spec.output_schema,
            path=f"{definition.name}.output",
        )

    assert unexplained == []


def test_skill_catalog_exposes_one_closed_read_contract() -> None:
    """Skill loading is one narrow Harness-owned behavior in the Catalog."""
    from restscope.tools import builtin_tool_catalog

    definitions = builtin_tool_catalog().definitions(subject="skill")

    assert [definition.name for definition in definitions] == ["skill.read"]
    spec = definitions[0].spec
    assert spec.input_schema["additionalProperties"] is False
    assert spec.input_schema["required"] == ["name"]
    assert spec.output_schema["additionalProperties"] is False


def test_global_http_contract_covers_only_generic_requests() -> None:
    """Scenario: the retired operation-scoped Probe shape is rejected."""
    from restscope.tools import builtin_tool_catalog

    schema = builtin_tool_catalog().get("restscope.http.request").spec.output_schema
    validate(
        {
            "status_code": 200,
            "reason_phrase": "OK",
            "url": "https://example.test/items",
            "headers": {"content-type": "application/json"},
            "body_format": "json",
            "body": {"items": []},
            "size_bytes": 12,
            "response_validation": "evaluated",
            "behavior_monitor_warnings": [],
        },
        schema,
    )
    with pytest.raises(ValidationError):
        validate({"case_id": "TC1", "status_code": 400}, schema)
