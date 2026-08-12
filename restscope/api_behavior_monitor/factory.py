"""Compose the redesigned API Response Monitor from App-owned collaborators."""

from __future__ import annotations

from restscope.api_behavior_monitor.catalog import ResponseMonitorCatalog
from restscope.config import RESTScopeConfig
from restscope.openapi_audit import OpenAPIAudit
from restscope.observability import TracingRuntime

from .coordinator import APIBehaviorMonitorCoordinator
from .resources import ResourceResponseTracker
from .response_contracts import ResponseContractTracker
from .system_agents import SystemAgentRunner


def build_api_behavior_monitor_coordinator(
    config: RESTScopeConfig,
    *,
    catalog: ResponseMonitorCatalog,
    openapi_audit: OpenAPIAudit,
    system_agent_runner: SystemAgentRunner,
    tracing_runtime: TracingRuntime | None = None,
) -> APIBehaviorMonitorCoordinator:
    """Connect Contract, observation, and Resource Monitor stages.

    The Catalog is the single persistence Interface for raw successful JSON,
    resource state, source propositions, and abstract test cases.  The System
    Agent runner remains Harness-owned and is used only when a new response
    group needs identity-field judgment.
    """

    runtime = tracing_runtime or TracingRuntime.disabled()
    runtime.redactor.register_secrets(
        (
            config.llm.thinking.api_key,
            config.llm.fast.api_key,
        )
    )
    return APIBehaviorMonitorCoordinator(
        contract_tracker=ResponseContractTracker(openapi_audit),
        catalog=catalog,
        resource_tracker=ResourceResponseTracker(
            catalog=catalog,
            system_agent_runner=system_agent_runner,
            tracing_runtime=runtime,
        ),
        tracing_runtime=runtime,
    )
