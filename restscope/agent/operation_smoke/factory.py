"""Configured construction for the Operation Smoke Agent."""

from __future__ import annotations

from restscope.agent.parameter_patch import ParameterPatchAgentFactory
from restscope.capabilities import ToolExecutor
from restscope.llm import LLMClient, ModelSelector, build_llm_client
from restscope.observability import TracingRuntime
from restscope.restscope_config import RESTScopeConfig
from restscope.testing import GeneratorConfigCatalog, ReferenceValueProvider

from .agent import OperationBatchRunner, OperationSmokeAgent
from .diagnosis import OperationSmokeDiagnoser
from .grouping import PatchGroupPlanner
from .probe import CurrentOperationHTTPProbe


def build_operation_smoke_agent(
    config: RESTScopeConfig,
    *,
    config_catalog: GeneratorConfigCatalog,
    batch_runner: OperationBatchRunner,
    reference_values: ReferenceValueProvider,
    tool_executor: ToolExecutor | None = None,
    llm_client: LLMClient | None = None,
    tracing_runtime: TracingRuntime | None = None,
) -> OperationSmokeAgent:
    """Build the smoke loop with Thinking analysis and Fast patch compilation."""

    runtime = tracing_runtime or TracingRuntime.disabled()
    client = llm_client or build_llm_client(
        config.llm,
        tracing_runtime=runtime,
    )
    selector = ModelSelector.from_config(config.llm)
    return OperationSmokeAgent(
        config_catalog=config_catalog,
        batch_runner=batch_runner,
        diagnoser=OperationSmokeDiagnoser(
            client=client,
            planning_model=selector.select(
                "operation_smoke_root_cause_diagnosis"
            ),
            effect_model=selector.select(
                "operation_smoke_effect_validation"
            ),
            http_probe=(
                CurrentOperationHTTPProbe(tool_executor)
                if tool_executor is not None
                else None
            ),
            tracing_runtime=runtime,
        ),
        group_planner=PatchGroupPlanner(),
        patch_agent_factory=ParameterPatchAgentFactory(
            client=client,
            model=selector.select("parameter_patch_agent"),
            tracing_runtime=runtime,
        ),
        reference_values=reference_values,
        tracing_runtime=runtime,
    )
