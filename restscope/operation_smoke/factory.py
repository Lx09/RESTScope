"""Compose Resolution with nested Compact, Patch, and Review Agents."""

from __future__ import annotations

from restscope.operation_smoke.failure_resolution import (
    FailureResolutionAgent,
    FailureResolutionFinalizer,
)
from collections.abc import Callable

from restscope.tools.context import ToolContext
from restscope.tools.http import CurrentOperationHTTPProbe, TargetHTTPRequestTool
from restscope.tools.openapi import OpenAPIToolBackend
from restscope.tools.resource import ResourceToolBackend
from restscope.operation_smoke.parameter_patch import (
    ParameterPatchCoordinatorFactory,
)
from restscope.operation_smoke.memory import SmokeMemory, SmokePatchApplication
from restscope.operation_smoke.memory.ports import SmokeMemoryUnitOfWorkFactory
from restscope.llm import LLMClient, ModelSelector, build_llm_client
from restscope.observability import TracingRuntime
from restscope.request_generation import SeededRandom
from restscope.config import RESTScopeConfig
from restscope.request_generation import RequestGenerationConfigStore

from .coordinator import OperationSmokeCoordinator, SmokeBatchRunner
from .references import BehaviorMonitorReferenceValues


def build_operation_smoke_coordinator(
    config: RESTScopeConfig,
    *,
    config_store: RequestGenerationConfigStore,
    batch_runner: SmokeBatchRunner,
    reference_values: BehaviorMonitorReferenceValues,
    http_tool: TargetHTTPRequestTool,
    context_provider: Callable[[], ToolContext],
    openapi_backend: OpenAPIToolBackend,
    resource_backend: ResourceToolBackend | None = None,
    unit_of_work_factory: SmokeMemoryUnitOfWorkFactory,
    llm_client: LLMClient | None = None,
    tracing_runtime: TracingRuntime | None = None,
) -> OperationSmokeCoordinator:
    """Build Resolution, nested Compact/Patch/Review, and App-lifetime Memory.

    Draft worklists and candidates remain run-local. The Resolution finalizer
    receives the shared Unit of Work factory so decided Failures, Attempts,
    Generator/Constraint state, and change events commit together.
    """
    runtime = tracing_runtime or TracingRuntime.disabled()
    client = llm_client or build_llm_client(
        config.llm,
        tracing_runtime=runtime,
    )
    selector = ModelSelector.from_config(config.llm)
    memory = SmokeMemory(unit_of_work_factory)
    patch_application = SmokePatchApplication(unit_of_work_factory)
    patch_coordinator_factory = ParameterPatchCoordinatorFactory(
        client=client,
        patch_model=selector.select("parameter_patch_agent"),
        review_model=selector.select("parameter_patch_review_agent"),
        openapi_backend=openapi_backend,
        resource_backend=resource_backend,
        tracing_runtime=runtime,
    )
    return OperationSmokeCoordinator(
        config_store=config_store,
        batch_runner=batch_runner,
        failure_resolution_agent=FailureResolutionAgent(
            client=client,
            model=selector.select("operation_smoke_failure_resolution"),
            compact_model=selector.select(
                "operation_smoke_failure_resolution_compact"
            ),
            http_probe=CurrentOperationHTTPProbe(
                http_tool=http_tool,
                context_provider=context_provider,
            ),
            memory=memory,
            patch_coordinator_factory=patch_coordinator_factory,
            finalizer=FailureResolutionFinalizer(unit_of_work_factory),
            reference_values=reference_values,
            openapi_backend=openapi_backend,
            tracing_runtime=runtime,
        ),
        constraint_reader=patch_application,
        reference_values=reference_values,
        random_seed=SeededRandom(config.random.seed).seed,
        tracing_runtime=runtime,
    )
