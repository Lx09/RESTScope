"""Protect Profile-authorized, session-private generic Agent Plans."""

from __future__ import annotations

import json

import pytest
from jsonschema import ValidationError as JSONSchemaValidationError
from jsonschema import validate

from tests.agent_helpers import start_test_agent


def _plan(step: str, status: str = "pending") -> dict[str, object]:
    """Build one model-facing Plan item for readable test scenarios."""
    return {"step": step, "status": status}


def test_plan_tools_publish_closed_bounded_schemas() -> None:
    """A Profile can review the complete generic Plan contract before launch."""
    from restscope.tools import builtin_tool_catalog

    definitions = builtin_tool_catalog().definitions(subject="plan")

    assert [definition.name for definition in definitions] == [
        "plan.read",
        "plan.update",
    ]
    read_spec, update_spec = [definition.spec for definition in definitions]
    example = {
        "explanation": "Update progress after the focused tests.",
        "plan": [
            _plan("Locate the issue", "completed"),
            _plan("Implement the fix", "in_progress"),
            _plan("Run tests"),
        ],
    }

    validate({}, read_spec.input_schema)
    validate(example, update_spec.input_schema)
    validate(example, update_spec.output_schema)
    assert update_spec.input_schema["additionalProperties"] is False
    assert update_spec.input_schema["properties"]["plan"]["maxItems"] == 100
    assert set(update_spec.output_schema["required"]) == {"explanation", "plan"}

    invalid_values = (
        {**example, "unexpected": True},
        {"plan": [_plan("Unknown state", "blocked")]},
        {
            "plan": [
                _plan("First", "in_progress"),
                _plan("Second", "in_progress"),
            ]
        },
        {"plan": [_plan("x" * 1_001)]},
        {"explanation": "x" * 2_001, "plan": []},
        {"explanation": None, "plan": []},
    )
    for value in invalid_values:
        with pytest.raises(JSONSchemaValidationError):
            validate(value, update_spec.input_schema)


def test_plan_store_replaces_complete_state_and_returns_defensive_copies() -> None:
    """One Agent may rewrite or clear its Plan without exposing mutable state."""
    from restscope.tools.plan import AgentPlan, AgentPlanItem, AgentPlanStore

    store = AgentPlanStore()

    first_read = store.read()
    second_read = store.read()
    updated = store.replace(
        AgentPlan(
            explanation="Begin implementation.",
            plan=(AgentPlanItem(step="Implement", status="in_progress"),),
        )
    )
    cleared = store.replace(AgentPlan(explanation=None, plan=()))

    assert first_read.model_dump(mode="json") == {
        "explanation": None,
        "plan": [],
    }
    assert first_read is not second_read
    assert updated.model_dump(mode="json")["plan"] == [
        _plan("Implement", "in_progress")
    ]
    assert cleared.model_dump(mode="json") == {"explanation": None, "plan": []}
    assert store.read() is not cleared


def test_plan_toolbox_rejects_bad_updates_without_mutating_state() -> None:
    """Schema and cross-field mistakes leave the Agent's private Plan untouched."""
    from restscope.llm import ToolCall
    from restscope.tools import AgentToolbox, ToolCatalog, builtin_tool_catalog
    from restscope.tools.plan import AgentPlanStore, plan_tool_bindings

    names = ("plan.read", "plan.update")
    catalog = ToolCatalog(builtin_tool_catalog().select(names))
    store = AgentPlanStore()
    toolbox = AgentToolbox.from_catalog(
        catalog=catalog,
        selected_names=names,
        bindings=list(plan_tool_bindings(store)),
    )

    invalid = toolbox.execute(
        ToolCall(
            id="bad-plan",
            name="plan.update",
            arguments={
                "plan": [
                    _plan("First", "in_progress"),
                    _plan("Second", "in_progress"),
                ]
            },
        )
    )
    updated = toolbox.execute(
        ToolCall(
            id="good-plan",
            name="plan.update",
            arguments={"plan": [_plan("Run the focused tests", "in_progress")]},
        )
    )
    read = toolbox.execute(ToolCall(id="read-plan", name="plan.read", arguments={}))

    assert invalid.status == "denied"
    assert invalid.error["code"] == "invalid_tool_arguments"
    assert updated.status == "succeeded"
    assert updated.structured == {
        "explanation": None,
        "plan": [_plan("Run the focused tests", "in_progress")],
    }
    assert read.structured == updated.structured


class _PlanProvider:
    """Drive child Plan reads and writes without external model calls."""

    name = "scripted"

    def __init__(self) -> None:
        """Retain requests and the initial Plan observed by every child."""
        self.requests = []
        self.child_initial_plans: list[dict[str, object]] = []

    def invoke(self, request):
        """Read, update, and finish children while Main tasks finish directly."""
        from restscope.llm import LLMResponse, ToolCall

        self.requests.append(request)
        if request.metadata["role"] == "main":
            return LLMResponse(
                provider="scripted",
                model=request.model,
                parsed_json={"summary": "Main finished.", "findings": []},
                prompt_tokens=20,
                completion_tokens=10,
            )

        latest = request.messages[-1]
        if latest.role != "tool":
            return LLMResponse(
                provider="scripted",
                model=request.model,
                tool_calls=[ToolCall(id="child-read", name="plan.read", arguments={})],
                prompt_tokens=20,
                completion_tokens=10,
            )
        if latest.name == "plan.read":
            result = json.loads(latest.content)
            self.child_initial_plans.append(result["structured"])
            return LLMResponse(
                provider="scripted",
                model=request.model,
                tool_calls=[
                    ToolCall(
                        id="child-update",
                        name="plan.update",
                        arguments={
                            "explanation": "Child started its own work.",
                            "plan": [_plan("Child step", "in_progress")],
                        },
                    )
                ],
                prompt_tokens=20,
                completion_tokens=10,
            )
        return LLMResponse(
            provider="scripted",
            model=request.model,
            parsed_json={"summary": "Child finished.", "findings": []},
            prompt_tokens=20,
            completion_tokens=10,
        )


def _plan_runtime(*, profiles, binding_factories=()):
    """Build a local generic Agent runtime with no production Profile."""
    from restscope.harness import AgentRuntimeDefinition, build_harness
    from restscope.llm import LLMClient, LLMModelConfig
    from restscope.llm.registry import LLMProviderRegistry

    provider = _PlanProvider()
    providers = LLMProviderRegistry()
    providers.register(provider)
    runtime = build_harness(
        agent_runtime=AgentRuntimeDefinition(
            profiles=profiles,
            models=(
                LLMModelConfig(
                    name="thinking",
                    provider="scripted",
                    model="thinking-model",
                    max_tokens=256,
                    context_window_tokens=4_096,
                ),
            ),
            client=LLMClient(providers),
            tool_binding_factories=binding_factories,
        )
    )
    return runtime, provider


def test_profile_plan_grants_are_paired_and_harness_owned() -> None:
    """A partial grant or caller-supplied Plan Binding fails before model use."""
    from restscope.agent import AgentProfile
    from restscope.harness import ToolBindingFactory
    from restscope.tools import ToolBinding

    with pytest.raises(ValueError, match="both Plan Tools"):
        _plan_runtime(
            profiles=(
                AgentProfile(
                    name="main",
                    model_config_name="thinking",
                    reasoning_effort="none",
                    tool_names=("plan.read",),
                ),
            )
        )

    with pytest.raises(ValueError, match="Plan Tool Binding is owned by Harness"):
        _plan_runtime(
            profiles=(
                AgentProfile(
                    name="main",
                    model_config_name="thinking",
                    reasoning_effort="none",
                    tool_names=("plan.read", "plan.update"),
                ),
            ),
            binding_factories=(
                ToolBindingFactory(
                    name="plan.read",
                    create=lambda: ToolBinding(name="plan.read", execute=dict),
                ),
            ),
        )

    unselected_runtime, _provider = _plan_runtime(
        profiles=(
            AgentProfile(
                name="main", model_config_name="thinking", reasoning_effort="none"
            ),
        )
    )
    assert start_test_agent(unselected_runtime).toolbox.specs() == []


def test_each_agent_gets_one_private_plan_for_its_complete_session() -> None:
    """Worker roots and children cannot cross private Plan ownership."""
    from restscope.agent import AgentProfile, AgentTask
    from restscope.llm import ToolCall

    plan_tools = ("plan.read", "plan.update")
    subagent_tools = ("subagent.start", "subagent.wait", "subagent.cancel")
    runtime, provider = _plan_runtime(
        profiles=(
            AgentProfile(
                name="main",
                model_config_name="thinking",
                reasoning_effort="none",
                tool_names=plan_tools + subagent_tools,
                subagent_profile_names=("child",),
            ),
            AgentProfile(
                name="child",
                description="Own an isolated child Plan.",
                model_config_name="thinking",
                reasoning_effort="none",
                tool_names=plan_tools,
            ),
        )
    )
    main = start_test_agent(runtime)
    parent_update = main.toolbox.execute(
        ToolCall(
            id="parent-update",
            name="plan.update",
            arguments={
                "explanation": "Parent owns this Plan.",
                "plan": [_plan("Parent step", "in_progress")],
            },
        )
    )

    for index in range(2):
        started = main.toolbox.execute(
            ToolCall(
                id=f"start-{index}",
                name="subagent.start",
                arguments={
                    "profile_name": "child",
                    "objective": f"Run isolated child {index}.",
                },
            )
        )
        child_id = started.structured["subagent_id"]
        while True:
            waited = main.toolbox.execute(
                ToolCall(
                    id=f"wait-{index}",
                    name="subagent.wait",
                    arguments={"subagent_ids": [child_id], "timeout_seconds": 5},
                )
            )
            if waited.structured["agents"][0]["status"] == "completed":
                break

    first = main.run(AgentTask(objective="Finish the current task."))
    between_tasks = main.toolbox.execute(
        ToolCall(id="parent-read", name="plan.read", arguments={})
    )
    main.close()
    replacement = start_test_agent(runtime)
    replacement_plan = replacement.toolbox.execute(
        ToolCall(id="replacement-read", name="plan.read", arguments={})
    )

    assert parent_update.status == "succeeded"
    assert provider.child_initial_plans == [
        {"explanation": None, "plan": []},
        {"explanation": None, "plan": []},
    ]
    assert between_tasks.structured == parent_update.structured
    assert first.status == "completed"
    assert replacement_plan.structured == {"explanation": None, "plan": []}
