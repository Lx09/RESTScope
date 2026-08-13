"""Build the Agent Profiles owned by RESTScope's production App.

The module translates App model configuration into the Main Agent Profile and
the Resource Identifier System Agent registration. Harness remains responsible
for validating and running those definitions; composition only requests this
one App-specific runtime definition.
"""

from __future__ import annotations

from restscope.agent import AgentProfile, SystemAgentTask
from restscope.api_behavior_monitor.resource_identity import (
    IDENTIFIER_SYSTEM_AGENT_INSTRUCTIONS,
    IdentifierSelectionDecision,
    RESOURCE_IDENTIFIER_PROFILE_NAME,
    identifier_system_output_schema,
    validate_identifier_system_output,
)
from restscope.api_behavior_monitor.oracle import (
    INVALID_INPUT_ACCEPTED_PROFILE,
    ORACLE_SYSTEM_AGENT_INSTRUCTIONS,
    RESPONSE_SCHEMA_MISMATCH_PROFILE,
    VALID_INPUT_SERVER_ERROR_PROFILE,
    OracleConfirmationDecision,
    oracle_output_schema,
    validate_oracle_output,
)
from restscope.config import RESTScopeConfig
from restscope.harness import AgentRuntimeDefinition, SystemAgentDefinition
from restscope.llm import build_llm_client, build_llm_model_config
from restscope.observability import TracingRuntime
from restscope.tools.plan import PLAN_READ_TOOL_NAME, PLAN_UPDATE_TOOL_NAME


_PATCH_PROFILE_NAME = "parameter-patch"
_MAIN_SKILLS = ("explore-api-behavior", "resolve-operation-failures")
_MAIN_TOOLS = (
    PLAN_READ_TOOL_NAME,
    PLAN_UPDATE_TOOL_NAME,
    "openapi.list_operations",
    "openapi.list_inputs",
    "openapi.list_response_fields",
    "openapi.get_input_schema",
    "openapi.get_response_field_schema",
    "request_generation.get_input_state",
    "test_case.run_batch",
    "test_case.get_batch_results",
    "test_case.get",
    "restscope.http.request",
    "subagent.start",
    "subagent.wait",
    "subagent.cancel",
    "file.read",
)
_PATCH_TOOLS = (
    "file.read",
    "resource.list_resources",
    "resource.list_ids",
    "openapi.find_observed_response_fields",
    "request_generation.get_input_state",
    "request_generation.validate_patch",
    "parameter_patch.apply",
)


_MAIN_PROFILE_INSTRUCTIONS = """You are RESTScope's single long-lived Main Agent.

- Work on the API target already initialized for this App lifetime. Treat these
  Profile instructions as the continuing mission; there is no separate task
  request or user-authored objective at startup.
- Own every semantic workflow decision. Decide what to investigate, which
  authorized Skills to load, which Tools or Subagents to use, what order to
  follow, whether another attempt is useful, and when to finish.
- Find reproducible happy paths, including useful resource states, then perform
  exceptional testing to discover as many replay-confirmed Bugs as practical.
  Happy-path discovery guides the order but never blocks worthwhile exceptional
  testing.
- Inspect authorized Skill metadata and load the Skills relevant to the current
  work. Skills provide methods; they do not grant access or override this
  Profile or the Harness contract.
- Initialize the private Plan for the current work and revise it as evidence
  changes. The Plan is working memory, not evidence, a scheduler, or persistent
  state.
- Use a child Profile only when its described capability fits a bounded piece
  of the work. Supply a complete objective and required evidence because the
  child receives no parent conversation or hidden state.
- Base factual conclusions on current authorized Tool or Subagent results.
  Never invent evidence references or treat a plan, prior belief, Skill text,
  OpenAPI description, or successful Tool execution as proof of an API outcome.
- Do not repeat an action unless new evidence, changed state, or a specific
  predicted benefit makes the next attempt materially different.
- Finish when the current authorized capabilities cannot make meaningful safe
  progress. Report unsupported, blocked, safety-skipped, and unresolved work
  explicitly.
- Return only the required bounded AgentCompletion result.
"""


def _build_agent_runtime_definition(
    config: RESTScopeConfig,
    *,
    tracing_runtime: TracingRuntime,
) -> AgentRuntimeDefinition | None:
    """Compose the App-authorized Main and Monitor System Agent Profiles.

    Args:
        config: Validated model-provider configuration for the process.
        tracing_runtime: App-owned tracing used by the shared model client.

    Returns:
        The Profiles, models, client, and System Agent contracts enabled by the
        configuration, or ``None`` when neither configured model is enabled.
    """
    thinking = build_llm_model_config("thinking", config.llm.thinking)
    fast = build_llm_model_config("fast", config.llm.fast)
    profiles: list[AgentProfile] = []
    system_agents: list[SystemAgentDefinition] = []
    models = []
    if thinking.enabled:
        models.append(thinking)
        profiles.append(
            AgentProfile(
                name="main",
                instructions=_MAIN_PROFILE_INSTRUCTIONS,
                model_config_name="thinking",
                tool_names=_MAIN_TOOLS,
                skill_names=_MAIN_SKILLS,
                subagent_profile_names=(_PATCH_PROFILE_NAME,),
            )
        )
        profiles.append(
            AgentProfile(
                name=_PATCH_PROFILE_NAME,
                description=(
                    "Build, validate, apply, and verify one bounded request "
                    "Generation Parameter Patch using apply-parameter-patch."
                ),
                model_config_name="thinking",
                tool_names=_PATCH_TOOLS,
                skill_names=("apply-parameter-patch",),
            )
        )
    if fast.enabled:
        models.append(fast)
        profiles.append(
            AgentProfile(
                name=RESOURCE_IDENTIFIER_PROFILE_NAME,
                instructions=IDENTIFIER_SYSTEM_AGENT_INSTRUCTIONS,
                model_config_name="fast",
            )
        )
        system_agents.append(
            SystemAgentDefinition(
                profile_name=RESOURCE_IDENTIFIER_PROFILE_NAME,
                adapt_task=SystemAgentTask.model_validate,
                output_model=IdentifierSelectionDecision,
                build_output_schema=identifier_system_output_schema,
                validate_output=validate_identifier_system_output,
                output_schema_name="IdentifierSelectionDecision",
            )
        )
        for profile_name in (
            VALID_INPUT_SERVER_ERROR_PROFILE,
            INVALID_INPUT_ACCEPTED_PROFILE,
            RESPONSE_SCHEMA_MISMATCH_PROFILE,
        ):
            profiles.append(
                AgentProfile(
                    name=profile_name,
                    instructions=ORACLE_SYSTEM_AGENT_INSTRUCTIONS,
                    model_config_name="fast",
                )
            )
            system_agents.append(
                SystemAgentDefinition(
                    profile_name=profile_name,
                    adapt_task=SystemAgentTask.model_validate,
                    output_model=OracleConfirmationDecision,
                    build_output_schema=oracle_output_schema,
                    validate_output=validate_oracle_output,
                    output_schema_name="OracleConfirmationDecision",
                )
            )
    if not profiles:
        return None
    return AgentRuntimeDefinition(
        profiles=tuple(profiles),
        models=tuple(models),
        client=build_llm_client(config.llm, tracing_runtime=tracing_runtime),
        system_agents=tuple(system_agents),
    )
