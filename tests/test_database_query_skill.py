"""Protect the packaged database-query Skill and lazy category References."""

from __future__ import annotations

from importlib.resources import files

import pytest
import yaml

SKILL_NAME = "query-restscope-database"
EXPECTED_REFERENCES = (
    "references/schema-discovery.md",
    "references/progress-and-coverage.md",
    "references/batches-and-observations.md",
    "references/bugs-and-oracles.md",
    "references/resources-and-states.md",
    "references/inputs-and-generation.md",
    "references/openapi-and-contract-changes.md",
)


def test_database_query_skill_manifest_and_categories_are_exact() -> None:
    """The installed Skill declares only its query and lazy Reference dependencies."""

    from restscope.skills import builtin_skill_catalog

    skill = builtin_skill_catalog().get(SKILL_NAME)
    assert skill.manifest.version == "1.0"
    assert skill.manifest.risk_level == "medium"
    assert skill.manifest.required_tools == ("file.read", "database.query")
    assert skill.manifest.required_context_sources == ()
    assert tuple(reference.path for reference in skill.references) == EXPECTED_REFERENCES

    root = files("restscope.builtin_skills").joinpath(SKILL_NAME)
    source = root.joinpath("SKILL.md").read_text(encoding="utf-8")
    frontmatter = yaml.safe_load(source.split("---", 2)[1])
    runtime = yaml.safe_load(root.joinpath("restscope.yaml").read_text(encoding="utf-8"))
    assert frontmatter == {
        "name": SKILL_NAME,
        "description": skill.manifest.description,
    }
    assert runtime == {
        "version": "1.0",
        "risk_level": "medium",
        "required_tools": ["file.read", "database.query"],
        "required_context_sources": [],
    }


def test_database_query_skill_routes_every_purpose_and_preserves_evidence_meaning() -> None:
    """Each current evidence purpose has one direct Reference and guarded interpretation."""

    from restscope.skills import builtin_skill_catalog

    skill = builtin_skill_catalog().get(SKILL_NAME)
    combined = "\n".join(
        [skill.instructions, *(reference.content for reference in skill.references)]
    )
    for term in (
        "sqlite_schema",
        "positive_attempts",
        "response_headers",
        "oracle_assessments.is_bug",
        "resource_state_events",
        "operation_input_sources",
        "openapi_change_events",
    ):
        assert term in combined
    assert "test-progress" in skill.instructions
    assert "single complete selected" in skill.instructions
    assert "not as a plan" in skill.instructions
    assert "current mutable generation configuration" in combined
    assert "response_headers, observation_id" not in combined


def test_one_reference_enters_context_only_after_file_read() -> None:
    """Loading the Skill body does not eagerly inject any category document."""

    from restscope.agent import AgentProfile, AgentTask
    from restscope.db import create_engine_from_url
    from restscope.harness import AgentRuntimeDefinition, build_harness
    from restscope.llm import LLMClient, LLMModelConfig, LLMResponse, ToolCall
    from restscope.llm.registry import LLMProviderRegistry
    from restscope.tools.database import DatabaseQueryToolBackend
    from tests.agent_helpers import start_test_agent

    class Provider:
        """Return one Skill read, one Reference read, then completion."""

        name = "scripted"

        def __init__(self) -> None:
            self.requests: list[object] = []
            self.responses = [
                LLMResponse(
                    provider=self.name,
                    model="query-model",
                    tool_calls=[
                        ToolCall(
                            id="read-skill",
                            name="skill.read",
                            arguments={"name": SKILL_NAME},
                        )
                    ],
                ),
                LLMResponse(
                    provider=self.name,
                    model="query-model",
                    tool_calls=[
                        ToolCall(
                            id="read-reference",
                            name="file.read",
                            arguments={
                                "skill_name": SKILL_NAME,
                                "path": "references/progress-and-coverage.md",
                            },
                        )
                    ],
                ),
                LLMResponse(
                    provider=self.name,
                    model="query-model",
                    parsed_json={"summary": "Done", "findings": []},
                ),
            ]

        def invoke(self, request):
            """Record each complete prompt before returning its scripted result."""

            self.requests.append(request)
            return self.responses.pop(0)

    provider = Provider()
    registry = LLMProviderRegistry()
    registry.register(provider)
    engine = create_engine_from_url("sqlite:///:memory:")
    runtime = build_harness(
        database_query_backend=DatabaseQueryToolBackend(engine=engine),
        agent_runtime=AgentRuntimeDefinition(
            profiles=(
                AgentProfile(
                    name="query",
                    model_config_name="query-model",
                    tool_names=("database.query", "file.read"),
                    skill_names=(SKILL_NAME,),
                ),
            ),
            models=(
                LLMModelConfig(
                    name="query-model",
                    provider="scripted",
                    model="query-model",
                    max_tokens=256,
                    context_window_tokens=8_192,
                ),
            ),
            client=LLMClient(registry),
        ),
    )

    result = start_test_agent(runtime, "query").run(
        AgentTask(objective="Assess current positive and negative coverage.")
    )

    assert result.status == "completed"
    prompts = [
        "\n".join(message.content for message in request.messages)
        for request in provider.requests
    ]
    assert "Operation-level Batch progress" not in prompts[0]
    assert "Operation-level Batch progress" not in prompts[1]
    assert "Operation-level Batch progress" in prompts[2]
    assert "Exact operation input sources" not in prompts[2]


@pytest.mark.parametrize("missing_tool", ("file.read", "database.query"))
def test_profile_missing_a_database_skill_dependency_fails_startup(
    missing_tool: str,
) -> None:
    """A Profile cannot select the method without every required capability."""

    from restscope.agent import AgentProfile
    from restscope.harness import AgentRuntimeDefinition, build_harness
    from restscope.llm import LLMClient, LLMModelConfig
    from restscope.llm.registry import LLMProviderRegistry

    class Provider:
        """Exist only so dependency validation reaches the selected Skill."""

        name = "unused"

    registry = LLMProviderRegistry()
    registry.register(Provider())
    granted = tuple(
        name for name in ("file.read", "database.query") if name != missing_tool
    )

    with pytest.raises(ValueError, match=f"{SKILL_NAME} requires Tool {missing_tool}"):
        build_harness(
            agent_runtime=AgentRuntimeDefinition(
                profiles=(
                    AgentProfile(
                        name="query",
                        model_config_name="unused",
                        tool_names=granted,
                        skill_names=(SKILL_NAME,),
                    ),
                ),
                models=(
                    LLMModelConfig(
                        name="unused",
                        provider="unused",
                        model="unused",
                        max_tokens=128,
                        context_window_tokens=4_096,
                    ),
                ),
                client=LLMClient(registry),
            )
        )
