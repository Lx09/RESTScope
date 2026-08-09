"""Configured construction for the API Behavior Monitor Coordinator."""

from __future__ import annotations

from restscope.openapi_audit import OpenAPIAudit
from restscope.llm import LLMClient, ModelSelector, build_llm_client
from restscope.observability import TracingRuntime
from restscope.config import RESTScopeConfig

from .coordinator import APIBehaviorMonitorCoordinator
from .response_contracts import ResponseContractTracker
from .resource_identifiers import ResourceCatalog, ResourceIdentifierTracker
from .response_values import ResponseValueCatalog, ResponseValueTracker


def build_api_behavior_monitor_coordinator(
    config: RESTScopeConfig,
    *,
    resource_catalog: ResourceCatalog,
    response_value_catalog: ResponseValueCatalog,
    openapi_audit: OpenAPIAudit,
    llm_client: LLMClient | None = None,
    tracing_runtime: TracingRuntime | None = None,
) -> APIBehaviorMonitorCoordinator:
    """Connect contract, identifier, and response-value monitoring modules.

    The supplied Catalogs and OpenAPI Audit share the App's database session
    factory but retain separate domain Interfaces. The optional client and
    tracing runtime let tests inject deterministic collaborators.
    """
    runtime = tracing_runtime
    if runtime is None and llm_client is not None:
        runtime = llm_client.tracing_runtime
    runtime = runtime or TracingRuntime.disabled()
    runtime.redactor.register_secrets(
        (
            config.llm.thinking.api_key,
            config.llm.fast.api_key,
        )
    )
    client = llm_client or build_llm_client(
        config.llm,
        tracing_runtime=runtime,
    )
    client.tracing_runtime = runtime
    model = ModelSelector.from_config(config.llm).select(
        "api_behavior_monitor"
    )
    resource_tracker = ResourceIdentifierTracker(
        catalog=resource_catalog,
        client=client,
        model=model,
        tracing_runtime=runtime,
    )
    return APIBehaviorMonitorCoordinator(
        contract_tracker=ResponseContractTracker(openapi_audit),
        resource_identifier_tracker=resource_tracker,
        response_value_tracker=ResponseValueTracker(
            catalog=response_value_catalog,
            client=client,
            model=model,
            tracing_runtime=runtime,
        ),
        tracing_runtime=runtime,
    )
