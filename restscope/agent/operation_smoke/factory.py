"""Configured construction for the Operation Smoke Agent."""

from __future__ import annotations

from restscope.llm import LLMClient, ModelSelector, build_llm_client
from restscope.observability import TracingRuntime
from restscope.restscope_config import RESTScopeConfig
from restscope.testing import GeneratorConfigCatalog, ReferenceValueProvider

from .agent import OperationBatchRunner, OperationSmokeAgent
from .diagnosis import OperationSmokeDiagnoser


def build_operation_smoke_agent(
    config: RESTScopeConfig,
    *,
    config_catalog: GeneratorConfigCatalog,
    batch_runner: OperationBatchRunner,
    reference_values: ReferenceValueProvider,
    llm_client: LLMClient | None = None,
    tracing_runtime: TracingRuntime | None = None,
) -> OperationSmokeAgent:
    """Build the smoke loop with the configured shared FAST model."""

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
            model=ModelSelector.from_config(config.llm).select(
                "operation_smoke_parameter_diagnosis"
            ),
        ),
        reference_values=reference_values,
    )
