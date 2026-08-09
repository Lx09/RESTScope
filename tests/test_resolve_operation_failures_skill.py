"""Protect operation Failure Resolution as a standard Subagent-delegating Skill."""

from __future__ import annotations

from importlib.resources import files
import json
from typing import Any

import pytest
import yaml


_BUILD_SKILL_NAME = "build-parameter-patch"
_RESOLVE_SKILL_NAME = "resolve-operation-failures"
_SUBAGENT_TOOLS = frozenset(
    {"subagent.start", "subagent.wait", "subagent.cancel"}
)


class _SkillProvider:
    """Return local scripted responses while retaining exact Agent requests."""

    name = "scripted"

    def __init__(self, responses: list[Any] | None = None) -> None:
        """Store optional responses; validation-only tests never invoke them."""
        self.responses = list(responses or [])
        self.requests: list[Any] = []

    def invoke(self, request):
        """Record one request and return the next deterministic response."""
        self.requests.append(request)
        return self.responses.pop(0)


def _model():
    """Return one enabled model configuration for parent and child fixtures."""
    from restscope.llm import LLMModelConfig

    return LLMModelConfig(
        role="fast",
        provider="scripted",
        model="fast-model",
        context_window_tokens=16_384,
        max_tokens=512,
    )


def _client(*responses):
    """Build a provider-neutral client without external model access."""
    from restscope.llm import LLMClient
    from restscope.llm.registry import LLMProviderRegistry

    provider = _SkillProvider(list(responses))
    registry = LLMProviderRegistry()
    registry.register(provider)
    return LLMClient(registry), provider


def _domain_binding_factories(names: tuple[str, ...]):
    """Bind only ordinary domain Tools; Harness owns file and child control."""
    from restscope.harness import ToolBindingFactory
    from restscope.tools import ToolBinding

    harness_owned = _SUBAGENT_TOOLS | {"file.read"}

    def factory(name: str) -> ToolBindingFactory:
        """Capture one exact Catalog name in its run-scoped factory."""
        return ToolBindingFactory(
            name=name,
            create=lambda: ToolBinding(name=name, execute=lambda **_arguments: {}),
        )

    return tuple(factory(name) for name in names if name not in harness_owned)


def _runtime(*, parent_tools: tuple[str, ...] | None = None, responses=()):
    """Build the approved parent-to-Patch-child Profile graph for tests."""
    from restscope.agent import AgentProfile
    from restscope.harness import AgentRuntimeDefinition, build_harness
    from restscope.skills import builtin_skill_catalog

    catalog = builtin_skill_catalog()
    resolution = catalog.get(_RESOLVE_SKILL_NAME)
    patch = catalog.get(_BUILD_SKILL_NAME)
    granted_parent = parent_tools or resolution.manifest.required_tools
    all_tools = tuple(dict.fromkeys((*granted_parent, *patch.manifest.required_tools)))
    client, provider = _client(*responses)
    runtime = build_harness(
        agent_runtime=AgentRuntimeDefinition(
            profiles=(
                AgentProfile(
                    name="resolution",
                    model_config_name="fast",
                    tool_names=granted_parent,
                    skill_names=(_RESOLVE_SKILL_NAME,),
                    subagent_profile_names=("patch-builder",),
                ),
                AgentProfile(
                    name="patch-builder",
                    description=(
                        "Build and self-review one fixed-scope Parameter Patch "
                        "with the build-parameter-patch Skill."
                    ),
                    model_config_name="fast",
                    tool_names=patch.manifest.required_tools,
                    skill_names=(_BUILD_SKILL_NAME,),
                ),
            ),
            models=(_model(),),
            client=client,
            tool_binding_factories=_domain_binding_factories(all_tools),
        )
    )
    return runtime, provider


def test_builtin_catalog_exposes_only_the_two_verb_led_skill_names() -> None:
    """The rename is a clean public break without a stale compatibility alias."""
    from restscope.skills import builtin_skill_catalog

    catalog = builtin_skill_catalog()

    assert tuple(skill.name for skill in catalog.definitions()) == (
        _BUILD_SKILL_NAME,
        _RESOLVE_SKILL_NAME,
    )
    with pytest.raises(KeyError):
        catalog.get("parameter-patch")


def test_resolution_skill_manifest_and_reference_library_are_exact() -> None:
    """Standard files alone must declare the bounded parent method and grants."""
    from restscope.skills import builtin_skill_catalog

    skill = builtin_skill_catalog().get(_RESOLVE_SKILL_NAME)
    expected_tools = (
        "file.read",
        "openapi.list_inputs",
        "openapi.list_response_fields",
        "openapi.get_input_schema",
        "openapi.get_response_field_schema",
        "test_case.get_parameter_value",
        "test_case.find_parameters_by_value",
        "test_case.get_response_field_value",
        "test_case.find_response_fields_by_value",
        "failure_resolution.read_worklist",
        "failure_resolution.write_worklist",
        "lookup_parameter_history",
        "restscope.http.request",
        "subagent.start",
        "subagent.wait",
        "subagent.cancel",
    )
    expected_references = (
        "references/evidence-and-diagnosis.md",
        "references/worklist-method.md",
        "references/tools-and-controlled-probes.md",
        "references/patch-subagent-delegation.md",
        "references/patch-review-and-decisions.md",
        "references/completion-checklist.md",
    )

    assert skill.manifest.name == _RESOLVE_SKILL_NAME
    assert skill.manifest.version == "1.0"
    assert skill.manifest.risk_level == "medium"
    assert skill.manifest.required_tools == expected_tools
    assert skill.manifest.required_context_sources == ()
    assert "generate_parameter_patch" not in expected_tools
    assert "parameter_patch.read_candidate" not in expected_tools
    assert tuple(reference.path for reference in skill.references) == expected_references
    assert 0 < len(skill.instructions) <= 24_000
    assert all(0 < len(reference.content) <= 24_000 for reference in skill.references)

    root = files("restscope.builtin_skills").joinpath(_RESOLVE_SKILL_NAME)
    source = root.joinpath("SKILL.md").read_text(encoding="utf-8")
    frontmatter = yaml.safe_load(source.split("---", 2)[1])
    manifest = yaml.safe_load(root.joinpath("restscope.yaml").read_text(encoding="utf-8"))
    assert frontmatter == {
        "name": _RESOLVE_SKILL_NAME,
        "description": skill.manifest.description,
    }
    assert manifest == {
        "version": "1.0",
        "risk_level": "medium",
        "required_tools": list(expected_tools),
        "required_context_sources": [],
    }


@pytest.mark.parametrize(
    "missing_tool",
    (
        "file.read",
        "openapi.list_inputs",
        "openapi.list_response_fields",
        "openapi.get_input_schema",
        "openapi.get_response_field_schema",
        "test_case.get_parameter_value",
        "test_case.find_parameters_by_value",
        "test_case.get_response_field_value",
        "test_case.find_response_fields_by_value",
        "failure_resolution.read_worklist",
        "failure_resolution.write_worklist",
        "lookup_parameter_history",
        "restscope.http.request",
        "subagent.start",
        "subagent.wait",
        "subagent.cancel",
    ),
)
def test_resolution_profile_rejects_every_missing_required_tool(
    missing_tool: str,
) -> None:
    """An incomplete parent grant must fail before either Agent can start."""
    from restscope.skills import builtin_skill_catalog

    required = builtin_skill_catalog().get(
        _RESOLVE_SKILL_NAME
    ).manifest.required_tools
    granted = tuple(name for name in required if name != missing_tool)

    with pytest.raises(ValueError):
        _runtime(parent_tools=granted)


def test_parent_metadata_and_resolution_references_load_progressively() -> None:
    """Parent context sees one Skill body and Reference only after each read."""
    from restscope.agent import AgentTask
    from restscope.llm import LLMResponse, ToolCall
    from restscope.skills import builtin_skill_catalog

    skill = builtin_skill_catalog().get(_RESOLVE_SKILL_NAME)
    reference = skill.reference("references/evidence-and-diagnosis.md")
    runtime, provider = _runtime(
        responses=(
            LLMResponse(
                provider="scripted",
                model="fast-model",
                tool_calls=[
                    ToolCall(
                        id="read-resolution-skill",
                        name="skill.read",
                        arguments={"name": _RESOLVE_SKILL_NAME},
                    )
                ],
            ),
            LLMResponse(
                provider="scripted",
                model="fast-model",
                tool_calls=[
                    ToolCall(
                        id="read-diagnosis-method",
                        name="file.read",
                        arguments={
                            "skill_name": _RESOLVE_SKILL_NAME,
                            "path": reference.path,
                        },
                    )
                ],
            ),
            LLMResponse(
                provider="scripted",
                model="fast-model",
                parsed_json={"summary": "Diagnosis method loaded.", "findings": []},
            ),
        )
    )

    result = runtime.start_main_agent("resolution").run(
        AgentTask(objective="Resolve the failed requests for one operation.")
    )

    assert result.status == "completed"
    stable = "\n".join(
        message.content for message in provider.requests[0].messages
    )
    assert _RESOLVE_SKILL_NAME in stable
    assert skill.manifest.description in stable
    assert "patch-builder" in stable
    assert skill.instructions not in stable
    assert reference.content not in stable
    assert builtin_skill_catalog().get(_BUILD_SKILL_NAME).manifest.description not in stable

    after_skill = "\n".join(
        message.content for message in provider.requests[1].messages
    )
    assert skill.instructions in after_skill
    assert reference.content not in after_skill

    after_reference = "\n".join(
        message.content for message in provider.requests[2].messages
    )
    assert json.dumps(reference.content)[1:-1] in after_reference


def test_parent_file_reader_cannot_open_the_child_patch_library() -> None:
    """Separate Profile selection prevents parent access to child References."""
    from restscope.llm import ToolCall

    runtime, _provider = _runtime()
    parent = runtime.start_main_agent("resolution")

    result = parent.toolbox.execute(
        ToolCall(
            id="cross-profile-read",
            name="file.read",
            arguments={
                "skill_name": _BUILD_SKILL_NAME,
                "path": "references/generators.md",
            },
        )
    )

    assert result.status == "failed"
    assert result.error["code"] == "skill_file_not_authorized"


def test_resolution_method_requires_patch_subagent_handoff_without_direct_generation() -> None:
    """Content preserves the approved parent diagnosis and child build boundary."""
    from restscope.skills import builtin_skill_catalog

    skill = builtin_skill_catalog().get(_RESOLVE_SKILL_NAME)
    combined = "\n".join(
        [skill.instructions, *(reference.content for reference in skill.references)]
    )

    assert "subagent.start" in combined
    assert "subagent.wait" in combined
    assert "subagent.cancel" in combined
    assert _BUILD_SKILL_NAME in combined
    assert "Do not call `generate_parameter_patch`" in combined
    assert "The completion is not a `P*`" in combined
    assert "keep the worklist item undecided" in combined
