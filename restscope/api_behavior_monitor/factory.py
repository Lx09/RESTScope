"""Compose the redesigned API Response Monitor from App-owned collaborators."""

from __future__ import annotations

from restscope.api_behavior_monitor.catalog import APIBehaviorCatalog
from restscope.config import RESTScopeConfig
from restscope.observability import TracingRuntime

from .contract_monitor import ResponseContractTracker
from .coordinator import APIBehaviorMonitorCoordinator
from .oracle import BugOracle
from .resource_monitor import ResourceResponseTracker, SystemAgentRunner


def build_api_behavior_monitor_coordinator(
    config: RESTScopeConfig,
    *,
    catalog: APIBehaviorCatalog,
    system_agent_runner: SystemAgentRunner,
    tracing_runtime: TracingRuntime | None = None,
) -> APIBehaviorMonitorCoordinator:
    """Connect Contract, observation, and Resource Monitor stages.

    The Catalog is the single persistence Interface for raw successful JSON,
    resource state, source propositions, and abstract test cases.  The System
    Agent runner remains Harness-owned and is used only when a new response
    group needs identity-field judgment or an operation/resource edge lacks its
    immutable result state.
    """

    runtime = tracing_runtime or TracingRuntime.disabled()
    runtime.redactor.register_secrets(config.llm.api_keys)
    return APIBehaviorMonitorCoordinator(
        contract_tracker=ResponseContractTracker(catalog),
        catalog=catalog,
        resource_tracker=ResourceResponseTracker(
            catalog=catalog,
            system_agent_runner=system_agent_runner,
            tracing_runtime=runtime,
        ),
        bug_oracle=BugOracle(catalog=catalog),
        tracing_runtime=runtime,
    )
