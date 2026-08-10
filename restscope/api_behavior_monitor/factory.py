"""Configured construction for the API Behavior Monitor Coordinator."""

from __future__ import annotations

from restscope.openapi_audit import OpenAPIAudit
from restscope.observability import TracingRuntime
from restscope.config import RESTScopeConfig

from .coordinator import APIBehaviorMonitorCoordinator
from .response_contracts import ResponseContractTracker
from .resource_identifiers import ResourceCatalog, ResourceIdentifierTracker
from .response_values import ResponseValueCatalog, ResponseValueTracker
from .system_agents import SystemAgentRunner


def build_api_behavior_monitor_coordinator(
    config: RESTScopeConfig,
    *,
    resource_catalog: ResourceCatalog,
    response_value_catalog: ResponseValueCatalog,
    openapi_audit: OpenAPIAudit,
    system_agent_runner: SystemAgentRunner,
    tracing_runtime: TracingRuntime | None = None,
) -> APIBehaviorMonitorCoordinator:
    """Connect contract, identifier, and response-value monitoring modules.

    The supplied Catalogs and OpenAPI Audit share the App's database session
    factory but retain separate domain Interfaces. The System Agent runner and
    optional tracing runtime let tests inject deterministic collaborators
    without giving Trackers direct model access.
    """
    runtime = tracing_runtime or TracingRuntime.disabled()
    runtime.redactor.register_secrets(
        (
            config.llm.thinking.api_key,
            config.llm.fast.api_key,
        )
    )
    resource_tracker = ResourceIdentifierTracker(
        catalog=resource_catalog,
        system_agent_runner=system_agent_runner,
        tracing_runtime=runtime,
    )
    return APIBehaviorMonitorCoordinator(
        contract_tracker=ResponseContractTracker(openapi_audit),
        resource_identifier_tracker=resource_tracker,
        response_value_tracker=ResponseValueTracker(
            catalog=response_value_catalog,
            system_agent_runner=system_agent_runner,
            tracing_runtime=runtime,
        ),
        tracing_runtime=runtime,
    )
