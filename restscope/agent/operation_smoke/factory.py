"""Configured construction for the Operation Smoke Agent."""

from __future__ import annotations

from restscope.capabilities import ToolExecutor
from restscope.llm import LLMClient, ModelSelector, build_llm_client
from restscope.observability import TracingRuntime
from restscope.restscope_config import RESTScopeConfig
from restscope.testing import GeneratorConfigCatalog, ReferenceValueProvider

from .agent import OperationBatchRunner, OperationSmokeAgent
from .diagnosis import OperationSmokeDiagnoser
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
    return OperationSmokeAgent(
        config_catalog=config_catalog,
        batch_runner=batch_runner,
        diagnoser=OperationSmokeDiagnoser(
            client=client,
            planning_model=ModelSelector.from_config(config.llm).select(
                "operation_smoke_plan_solve"
            ),
            patch_model=ModelSelector.from_config(config.llm).select(
                "operation_smoke_generator_patch"
            ),
            http_probe=(
                CurrentOperationHTTPProbe(tool_executor)
                if tool_executor is not None
                else None
            ),
            tracing_runtime=runtime,
        ),
        reference_values=reference_values,
        tracing_runtime=runtime,
    )
