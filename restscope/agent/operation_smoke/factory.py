"""Configured construction for the four-role Operation Smoke lifecycle."""

from __future__ import annotations

from restscope.agent.failure_solver import (
    CurrentOperationHTTPProbe,
    FailureSolveAgentFactory,
)
from restscope.agent.parameter_patch import ParameterPatchAgentFactory
from restscope.agent.smoke_effect import SmokeEffectAgent
from restscope.agent.smoke_plan import SmokePlanAgent
from restscope.capabilities import ToolExecutor
from restscope.llm import LLMClient, ModelSelector, build_llm_client
from restscope.observability import TracingRuntime
from restscope.restscope_config import RESTScopeConfig
from restscope.testing import GeneratorConfigCatalog

from .agent import OperationSmokeAgent, SmokeBatchRunner
from .references import BehaviorMonitorReferenceValues


def build_operation_smoke_agent(
    config: RESTScopeConfig,
    *,
    config_catalog: GeneratorConfigCatalog,
    batch_runner: SmokeBatchRunner,
    reference_values: BehaviorMonitorReferenceValues,
    tool_executor: ToolExecutor | None = None,
    llm_client: LLMClient | None = None,
    tracing_runtime: TracingRuntime | None = None,
) -> OperationSmokeAgent:
    """Build THINK Plan/Solve/Effect and FAST Patch roles for one App."""
    if tool_executor is None:
        raise ValueError(
            "Operation Smoke Failure Solve requires the scoped HTTP tool executor"
        )
    runtime = tracing_runtime or TracingRuntime.disabled()
    client = llm_client or build_llm_client(
        config.llm,
        tracing_runtime=runtime,
    )
    selector = ModelSelector.from_config(config.llm)
    return OperationSmokeAgent(
        config_catalog=config_catalog,
        batch_runner=batch_runner,
        plan_agent=SmokePlanAgent(
            client=client,
            model=selector.select("operation_smoke_plan"),
        ),
        failure_solver_factory=FailureSolveAgentFactory(
            client=client,
            model=selector.select("operation_smoke_failure_solve"),
            http_probe=CurrentOperationHTTPProbe(tool_executor),
        ),
        patch_agent_factory=ParameterPatchAgentFactory(
            client=client,
            model=selector.select("parameter_patch_agent"),
            tracing_runtime=runtime,
        ),
        effect_agent=SmokeEffectAgent(
            client=client,
            model=selector.select("operation_smoke_effect_validation"),
        ),
        reference_values=reference_values,
        tracing_runtime=runtime,
    )
