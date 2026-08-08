"""Compose Resolution with nested Compact, Patch, and Review Agents."""

from __future__ import annotations

from restscope.operation_smoke.failure_resolution import (
    FailureResolutionAgent,
    FailureResolutionFinalizer,
)
from restscope.tools.http import CurrentOperationHTTPProbe
from restscope.operation_smoke.parameter_patch import (
    ParameterPatchCoordinatorFactory,
)
from restscope.operation_smoke.memory import SmokeMemory, SmokePatchApplication
from restscope.harness import HarnessRuntime
from restscope.llm import LLMClient, ModelSelector, build_llm_client
from restscope.observability import TracingRuntime
from restscope.randomness import SeededRandom
from restscope.restscope_config import RESTScopeConfig
from restscope.harness.testing import GeneratorConfigCatalog
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
    harness_runtime: HarnessRuntime | None = None,
    llm_client: LLMClient | None = None,
    tracing_runtime: TracingRuntime | None = None,
) -> OperationSmokeCoordinator:
    """Build Resolution, nested Compact/Patch/Review, and App-lifetime Memory.

    Draft worklists and candidates remain run-local. The Resolution finalizer
    receives the shared Unit of Work factory so decided Failures, Attempts,
    Generator/Constraint state, and change events commit together.
    """
    if harness_runtime is None:
        raise ValueError(
            "Operation Smoke requires the App Harness runtime"
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
    patch_application = SmokePatchApplication(unit_of_work_factory)
    patch_coordinator_factory = ParameterPatchCoordinatorFactory(
        client=client,
        patch_model=selector.select("parameter_patch_agent"),
        review_model=selector.select("parameter_patch_review_agent"),
        openapi_capability=harness_runtime.openapi_capability,
        resource_capability=getattr(
            harness_runtime,
            "resource_identifier_capability",
            None,
        ),
        tracing_runtime=runtime,
    )
    return OperationSmokeCoordinator(
        config_catalog=config_catalog,
        batch_runner=batch_runner,
        failure_resolution_agent=FailureResolutionAgent(
            client=client,
            model=selector.select("operation_smoke_failure_resolution"),
            compact_model=selector.select(
                "operation_smoke_failure_resolution_compact"
            ),
            http_probe=CurrentOperationHTTPProbe(
                http_tool=harness_runtime.target_http_tool,
                context_provider=harness_runtime.require_context,
            ),
            memory=memory,
            patch_coordinator_factory=patch_coordinator_factory,
            finalizer=FailureResolutionFinalizer(unit_of_work_factory),
            reference_values=reference_values,
            openapi_capability=harness_runtime.openapi_capability,
            tracing_runtime=runtime,
        ),
        constraint_reader=patch_application,
        reference_values=reference_values,
        random_seed=SeededRandom(config.random.seed).seed,
        tracing_runtime=runtime,
    )
