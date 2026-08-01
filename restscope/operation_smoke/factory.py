"""Compose the three-Agent, memory-driven Operation Smoke workflow."""

from __future__ import annotations

from restscope.operation_smoke.failure_solver import (
    CurrentOperationHTTPProbe,
    FailureSolveAgentFactory,
)
from restscope.operation_smoke.parameter_patch import ParameterPatchAgentFactory
from restscope.operation_smoke.memory import SmokeMemory, SmokePatchApplication
from restscope.operation_smoke.failure_dedup import (
    FailureDedupAgent,
    FailureDeduplicator,
)
from restscope.capabilities import CapabilityRuntime
from restscope.llm import LLMClient, ModelSelector, build_llm_client
from restscope.observability import TracingRuntime
from restscope.randomness import SeededRandom
from restscope.restscope_config import RESTScopeConfig
from restscope.testing import GeneratorConfigCatalog
from restscope.db import (
    SqlAlchemySmokeMemoryUnitOfWork,
    create_engine_from_config,
    make_session_factory,
)

from .coordinator import OperationSmokeCoordinator, SmokeBatchRunner
from .references import BehaviorMonitorReferenceValues


def build_operation_smoke_coordinator(
    config: RESTScopeConfig,
    *,
    config_catalog: GeneratorConfigCatalog,
    batch_runner: SmokeBatchRunner,
    reference_values: BehaviorMonitorReferenceValues,
    capability_runtime: CapabilityRuntime | None = None,
    llm_client: LLMClient | None = None,
    tracing_runtime: TracingRuntime | None = None,
) -> OperationSmokeCoordinator:
    """Build Dedup, Solve, Patch, and their shared App-lifetime Memory.

    Dedup writes current Failures while Solve reads histories through the same
    deep Memory Interface. Patch application receives the same Unit of Work
    factory so Generator revision and Investigation commit atomically.
    """
    if capability_runtime is None:
        raise ValueError(
            "Operation Smoke requires the App capability runtime"
        )
    runtime = tracing_runtime or TracingRuntime.disabled()
    client = llm_client or build_llm_client(
        config.llm,
        tracing_runtime=runtime,
    )
    selector = ModelSelector.from_config(config.llm)
    engine = create_engine_from_config(config.db)
    session_factory = make_session_factory(engine)
    unit_of_work_factory = lambda: SqlAlchemySmokeMemoryUnitOfWork(
        session_factory
    )
    memory = SmokeMemory(unit_of_work_factory)
    patch_agent_factory = ParameterPatchAgentFactory(
        client=client,
        model=selector.select("parameter_patch_agent"),
        tracing_runtime=runtime,
    )
    return OperationSmokeCoordinator(
        config_catalog=config_catalog,
        batch_runner=batch_runner,
        failure_deduplicator=FailureDeduplicator(
            agent=FailureDedupAgent(
                client=client,
                model=selector.select("operation_smoke_failure_dedup"),
                operation_provider=capability_runtime.require_operation,
                tracing_runtime=runtime,
            ),
            memory=memory,
            tracing_runtime=runtime,
        ),
        failure_solver_factory=FailureSolveAgentFactory(
            client=client,
            model=selector.select("operation_smoke_failure_solve"),
            http_probe=CurrentOperationHTTPProbe(
                http_tool=capability_runtime.target_http_tool,
                context_provider=capability_runtime.require_context,
            ),
            memory=memory,
            patch_agent_factory=patch_agent_factory,
            patch_application=SmokePatchApplication(
                unit_of_work_factory
            ),
            reference_values=reference_values,
            tracing_runtime=runtime,
        ),
        reference_values=reference_values,
        random_seed=SeededRandom(config.random.seed).seed,
        tracing_runtime=runtime,
    )
