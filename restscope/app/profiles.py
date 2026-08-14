"""Build the Agent Profiles owned by RESTScope's production App.

The module translates App model configuration into Orchestrator, Task Executor,
Parameter Patch, Resource Identifier, and Resource State Profiles. Harness
remains responsible for validating and running those definitions; composition
only requests this one App-specific runtime definition.
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
from restscope.api_behavior_monitor.resource_state import (
    RESOURCE_STATE_PROFILE_NAME,
    RESOURCE_STATE_SYSTEM_AGENT_INSTRUCTIONS,
    ResourceStateDecision,
    resource_state_output_schema,
    validate_resource_state_output,
)
from restscope.config import RESTScopeConfig
from restscope.harness import (
    AgentRuntimeDefinition,
    ContextSourceBinding,
    SystemAgentDefinition,
)
from restscope.harness.test_progress import TEST_PROGRESS_CONTEXT_SOURCE
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
_DATABASE_QUERY_SKILL = "query-restscope-database"
_ORCHESTRATOR_TOOLS = ("database.query", "file.read")
_ORCHESTRATOR_SKILLS = (_DATABASE_QUERY_SKILL,)
_TASK_EXECUTOR_SKILLS = ("resolve-operation-failures", _DATABASE_QUERY_SKILL)
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
    "database.query",
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

- Use the Goal, Task Ledger projection, and current test-progress Context Source
  in this task. Treat test-progress as the default coverage summary. Load
  `query-restscope-database` and use `database.query` only when a planning,
  Replan, or completion decision needs narrower durable evidence.
- You have no child Profiles, testing or mutation Tools, or hidden history.
  Database rows are evidence, not permission to execute the next Task yourself.
- On the first call, return replan and create a small rolling set of verifiable
  Milestones. On later calls, return exactly one replan, dispatch_task, or
  complete decision.
- Plan REST API exploration across Tasks. Honor the run focus and confirmed
  prerequisites first. Prioritize operations without reproducible happy-path
  evidence, and prefer safe read-only discovery before work that may change
  target state.
- When an Operation lacks a required resource, identifier, or state, first
  dispatch one bounded prerequisite Task that can establish or discover it.
- After a reproducible happy path exists, plan worthwhile exceptional testing;
  exceptional evidence may justify earlier testing. Progress and resource-state
  counts identify coverage opportunities but never prove API behavior.
- Every testing Task must name one exact Operation, specify a `happy_path` or
  `exceptional` purpose, explain why it serves one current Milestone, and give
  evidence criteria the Task Executor can report exactly once. A prerequisite
  Task must likewise have one bounded objective.
- Use a `completed`, `partial`, `blocked`, or lifecycle failure result as follows:
  replan to complete a satisfied Milestone when useful work remains, or complete
  the run when none does; dispatch different follow-up work when missing evidence
  is still obtainable; replan when an assumption fails; and retry only when a
  method, state, or prerequisite materially changes.
- A skipped exceptional slot is unavailable coverage, not evidence. Count a Bug
  only when the Task result reports `bug_found` after replay confirmation.
- Replan when evidence changes what future work is useful. A replan may split,
  merge, reorder, supersede, or reopen work, but must change the future plan and
  must never change the Goal or rewrite prior Tasks or Attempts.
- Do not repeat a failed Task unchanged unless a new reason or changed state
  makes the next attempt materially different.
- Complete only when no reasonable safe coverage work remains. Assess every Goal
  criterion and current progress; every `unknown` or `not_met` verdict must also
  appear in unresolved work with its reason.
- Return only the required structured OrchestratorDecision.
"""

_TASK_EXECUTOR_PROFILE_INSTRUCTIONS = """You are RESTScope's Task Executor for one task.

- Work only on the Goal summary, current Milestone, single Task, success
  criteria, and selected Attempt history supplied in this fresh root call.
- Do not choose the next Operation, testing phase, cross-Task order, or overall
  coverage. Follow the assigned Operation, testing purpose, and criteria. If the
  assignment lacks enough information, return `blocked` rather than expanding
  its scope.
- Own execution inside that Task: choose authorized Tools, Parameter Patch child
  work, order, and evidence-driven retries. Load `resolve-operation-failures`
  when request or Batch evidence requires diagnosis.
- Keep failure recovery inside the Task while its criteria remain unchanged.
  After a Parameter Patch, run a fresh Batch; applying a Patch is not HTTP
  success. Return `partial` or `blocked` when authentication, permission,
  resource state, method, server, or another prerequisite cannot be resolved
  safely inside the assignment.
- A successful Tool call, skipped exceptional slot, or exceptional 2xx is not
  completion or Bug evidence. Report a Bug only when the result says
  `bug_found` after replay confirmation.
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
    test_progress_context: ContextSourceBinding | None,
) -> AgentRuntimeDefinition | None:
    """Compose App-authorized Orchestration and Monitor Agent Profiles.

    Args:
        config: Validated model-provider configuration for the process.
        tracing_runtime: App-owned tracing used by the shared model client.
        test_progress_context: Harness-owned bounded Reader required by the
            Orchestrator, or ``None`` when no thinking Profile can be built.

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
        if test_progress_context is None:
            raise ValueError("Orchestrator requires the test-progress Context Source")
        if test_progress_context.name != TEST_PROGRESS_CONTEXT_SOURCE:
            raise ValueError("Orchestrator Context Source must be test-progress")
        models.append(thinking)
        profiles.append(
            AgentProfile(
                name="orchestrator",
                instructions=_ORCHESTRATOR_INSTRUCTIONS,
                model_config_name="thinking",
                tool_names=_ORCHESTRATOR_TOOLS,
                skill_names=_ORCHESTRATOR_SKILLS,
                context_sources=(TEST_PROGRESS_CONTEXT_SOURCE,),
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
        profiles.append(
            AgentProfile(
                name=RESOURCE_STATE_PROFILE_NAME,
                instructions=RESOURCE_STATE_SYSTEM_AGENT_INSTRUCTIONS,
                model_config_name="fast",
            )
        )
        system_agents.append(
            SystemAgentDefinition(
                profile_name=RESOURCE_STATE_PROFILE_NAME,
                adapt_task=SystemAgentTask.model_validate,
                output_model=ResourceStateDecision,
                build_output_schema=resource_state_output_schema,
                validate_output=validate_resource_state_output,
                output_schema_name="ResourceStateDecision",
            )
        )
    if not profiles:
        return None
    return AgentRuntimeDefinition(
        profiles=tuple(profiles),
        models=tuple(models),
        client=build_llm_client(config.llm, tracing_runtime=tracing_runtime),
        system_agents=tuple(system_agents),
        context_sources=(
            (test_progress_context,)
            if thinking.enabled and test_progress_context is not None
            else ()
        ),
    )
