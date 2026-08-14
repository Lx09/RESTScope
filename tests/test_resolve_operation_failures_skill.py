"""Protect the inline-evidence Failure Resolution Skill and child boundary."""

from __future__ import annotations

from importlib.resources import files

import pytest
import yaml

RESOLVE = "resolve-operation-failures"
PATCH = "apply-parameter-patch"


def test_resolution_manifest_and_reference_library_are_exact() -> None:
    """The parent Skill declares only current inline diagnosis capabilities."""
    from restscope.skills import builtin_skill_catalog

    skill = builtin_skill_catalog().get(RESOLVE)
    expected_tools = (
        "file.read",
        "openapi.list_inputs",
        "openapi.list_response_fields",
        "openapi.get_input_schema",
        "openapi.get_response_field_schema",
        "request_generation.get_input_state",
        "test_case.run_batch",
        "restscope.http.request",
        "subagent.start",
        "subagent.wait",
        "subagent.cancel",
    )
    expected_references = (
        "references/evidence-and-diagnosis.md",
        "references/gather-and-test-evidence.md",
        "references/delegate-input-repair.md",
        "references/verify-repair-and-decide.md",
        "references/completion-checklist.md",
    )
    assert skill.manifest.version == "1.0"
    assert skill.manifest.risk_level == "medium"
    assert skill.manifest.required_tools == expected_tools
    assert tuple(item.path for item in skill.references) == expected_references

    root = files("restscope.builtin_skills").joinpath(RESOLVE)
    source = root.joinpath("SKILL.md").read_text(encoding="utf-8")
    frontmatter = yaml.safe_load(source.split("---", 2)[1])
    manifest = yaml.safe_load(root.joinpath("restscope.yaml").read_text(encoding="utf-8"))
    assert frontmatter == {
        "name": RESOLVE,
        "description": skill.manifest.description,
    }
    assert manifest["required_tools"] == list(expected_tools)


def test_resolution_delegates_application_and_verifies_real_effect() -> None:
    """Diagnosis delegates repair, confirms current state, and reruns tests."""
    from restscope.skills import builtin_skill_catalog

    skill = builtin_skill_catalog().get(RESOLVE)
    combined = "\n".join(
        [skill.instructions, *(item.content for item in skill.references)]
    )
    assert PATCH in combined
    assert all(name in combined for name in ("subagent.start", "subagent.wait", "subagent.cancel"))
    assert "request_generation.get_input_state" in combined
    assert "test_case.run_batch" in combined
    assert "Do not construct or rewrite" in combined
    assert "unresolved" in combined
    assert "grouped test-run Tool" in skill.instructions
    assert "each returned case is one actual generated request" in skill.instructions
    assert "request-input problems" in skill.manifest.description
    for role_term in (
        "Orchestrator",
        "Task Executor",
        "Profile",
        "Agent",
        "Subagent",
        "parent session",
        "child completion",
        "child Profile",
    ):
        assert role_term not in combined
    for retired in (
        "generate_parameter_patch",
        "parameter_patch.read_candidate",
        "lookup_parameter_history",
        "failure_resolution.read_worklist",
    ):
        assert retired not in combined


@pytest.mark.parametrize(
    "missing_tool",
    (
        "file.read",
        "openapi.list_inputs",
        "openapi.list_response_fields",
        "openapi.get_input_schema",
        "openapi.get_response_field_schema",
        "request_generation.get_input_state",
        "test_case.run_batch",
        "restscope.http.request",
        "subagent.start",
        "subagent.wait",
        "subagent.cancel",
    ),
)
def test_resolution_profile_rejects_each_missing_tool(missing_tool: str) -> None:
    """Skill dependency validation fails before parent startup."""
    from restscope.agent import AgentProfile
    from restscope.harness import AgentRuntimeDefinition, build_harness
    from restscope.llm import LLMClient, LLMModelConfig
    from restscope.llm.registry import LLMProviderRegistry
    from restscope.skills import builtin_skill_catalog

    required = builtin_skill_catalog().get(RESOLVE).manifest.required_tools
    granted = tuple(name for name in required if name != missing_tool)
    class Provider:
        """Exist only so Profile validation reaches Skill dependencies."""

        name = "unused"

    registry = LLMProviderRegistry()
    registry.register(Provider())
    with pytest.raises(ValueError):
        build_harness(
            agent_runtime=AgentRuntimeDefinition(
                profiles=(
                    AgentProfile(
                        name="resolution",
                        model_config_name="fast",
                        tool_names=granted,
                        skill_names=(RESOLVE,),
                        subagent_profile_names=("patch",),
                    ),
                    AgentProfile(
                        name="patch",
                        description="Apply one Patch with apply-parameter-patch.",
                        model_config_name="fast",
                    ),
                ),
                models=(
                    LLMModelConfig(
                        name="fast",
                        provider="unused",
                        model="unused",
                        max_tokens=256,
                        context_window_tokens=8_192,
                    ),
                ),
                client=LLMClient(registry),
            )
        )
