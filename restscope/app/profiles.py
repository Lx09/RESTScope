"""Build the Agent Profiles owned by RESTScope's production App.

The module translates App model configuration into Orchestrator, Task Executor,
Parameter Patch, and Resource Identifier Profiles. Harness remains responsible
for validating and running those definitions; composition only requests this
one App-specific runtime definition.
"""

from __future__ import annotations

from restscope.agent import AgentProfile, SystemAgentTask
from restscope.api_behavior_monitor.resource_identity import (
    IDENTIFIER_SYSTEM_AGENT_INSTRUCTIONS,
    RESOURCE_IDENTIFIER_PROFILE_NAME,
    IdentifierSelectionDecision,
    identifier_system_output_schema,
    validate_identifier_system_output,
)
from restscope.config import RESTScopeConfig
from restscope.harness import AgentRuntimeDefinition, SystemAgentDefinition
from restscope.llm import build_llm_client, build_llm_model_config
from restscope.observability import TracingRuntime
from restscope.orchestration.contracts import (
    orchestrator_output_schema,
    task_execution_output_schema,
    validate_orchestrator_output,
    validate_task_execution_output,
)
from restscope.orchestration.models import OrchestratorDecision, TaskExecutionResult
from restscope.tools.plan import PLAN_READ_TOOL_NAME, PLAN_UPDATE_TOOL_NAME

_PATCH_PROFILE_NAME = "parameter-patch"
_TASK_EXECUTOR_SKILLS = ("explore-api-behavior", "resolve-operation-failures")
_TASK_EXECUTOR_TOOLS = (
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


_ORCHESTRATOR_INSTRUCTIONS = """You are RESTScope's outer long-task Orchestrator.

- Use only the Goal and Task Ledger projection in the current task. You have no
  Tools, Skills, child Profiles, behavior database access, or hidden history.
- On the first call, return replan and create a small rolling set of verifiable
  Milestones. On later calls, return exactly one replan, dispatch_task, or
  complete decision.
- Every dispatched Task must name one current Milestone, explain why it serves
  the Goal, and give criterion IDs that the Task Executor can report exactly once.
- Replan when evidence changes what future work is useful. A replan may split,
  merge, reorder, supersede, or reopen work, but must change the future plan and
  must never change the Goal or rewrite prior Tasks or Attempts.
- Do not repeat a failed Task unchanged unless a new reason or changed state
  makes the next attempt materially different.
- Complete only after assessing every Goal criterion and naming unresolved work.
- Return only the required structured OrchestratorDecision.
"""

_TASK_EXECUTOR_PROFILE_INSTRUCTIONS = """You are RESTScope's Task Executor for one task.

- Work only on the Goal summary, current Milestone, single Task, success
  criteria, and selected Attempt history supplied in this fresh root call.
- Own semantic execution inside that Task: choose appropriate authorized Skills,
  Tools, Parameter Patch child work, order, and evidence-driven retries.
- Find reproducible happy-path or exceptional evidence required by the Task.
  Report a Bug only when the authorized behavior workflow confirms it by replay.
- Inspect authorized Skill metadata and load the Skills relevant to the current
  work. Skills provide methods; they do not grant access or override this
  Profile or the Harness contract.
- Use the private Plan only as short-lived intra-task working memory. It is not
  the outer Ledger, evidence, a scheduler, or cross-task memory.
- Use a child Profile only when its described capability fits a bounded piece
  of the work. Supply a complete objective and required evidence because the
  child receives no parent conversation or hidden state.
- Base factual conclusions on current authorized Tool or Subagent results.
  Never invent evidence references or treat a plan, prior belief, Skill text,
  OpenAPI description, or successful Tool execution as proof of an API outcome.
- Return one criterion verdict for every supplied criterion. State partial or
  blocked work explicitly, and never choose or propose the next Task.
- Return only the required structured TaskExecutionResult.
"""


def _build_agent_runtime_definition(
    config: RESTScopeConfig,
    *,
    tracing_runtime: TracingRuntime,
) -> AgentRuntimeDefinition | None:
    """Compose App-authorized Orchestration and Monitor Agent Profiles.

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
                name="orchestrator",
                instructions=_ORCHESTRATOR_INSTRUCTIONS,
                model_config_name="thinking",
            )
        )
        profiles.append(
            AgentProfile(
                name="task-executor",
                instructions=_TASK_EXECUTOR_PROFILE_INSTRUCTIONS,
                model_config_name="thinking",
                tool_names=_TASK_EXECUTOR_TOOLS,
                skill_names=_TASK_EXECUTOR_SKILLS,
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
        system_agents.extend(
            (
                SystemAgentDefinition(
                    profile_name="orchestrator",
                    adapt_task=SystemAgentTask.model_validate,
                    output_model=OrchestratorDecision,
                    build_output_schema=orchestrator_output_schema,
                    validate_output=validate_orchestrator_output,
                    output_schema_name="OrchestratorDecision",
                ),
                SystemAgentDefinition(
                    profile_name="task-executor",
                    adapt_task=SystemAgentTask.model_validate,
                    output_model=TaskExecutionResult,
                    build_output_schema=task_execution_output_schema,
                    validate_output=validate_task_execution_output,
                    output_schema_name="TaskExecutionResult",
                ),
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
    if not profiles:
        return None
    return AgentRuntimeDefinition(
        profiles=tuple(profiles),
        models=tuple(models),
        client=build_llm_client(config.llm, tracing_runtime=tracing_runtime),
        system_agents=tuple(system_agents),
    )
