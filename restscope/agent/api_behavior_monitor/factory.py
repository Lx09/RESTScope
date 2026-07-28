"""Configured construction for the API Behavior Monitor Agent."""

from __future__ import annotations

from restscope.db import (
    SqlAlchemyResourceCatalogUnitOfWork,
    SqlAlchemyResponseValueCatalogUnitOfWork,
    create_engine_from_config,
    make_session_factory,
)
from restscope.llm import LLMClient, ModelSelector, build_llm_client
from restscope.observability import TracingRuntime
from restscope.restscope_config import RESTScopeConfig

from .agent import APIBehaviorMonitorAgent
from .contract_tracker import ResponseContractTracker
from .resource_catalog import ResourceCatalog
from .resource_identifier import ResourceIdentifierTracker
from .response_value import ResponseValueTracker
from .response_value_catalog import ResponseValueCatalog


def build_api_behavior_monitor_agent(
    config: RESTScopeConfig,
    *,
    llm_client: LLMClient | None = None,
    tracing_runtime: TracingRuntime | None = None,
) -> APIBehaviorMonitorAgent:
    """
    Build api behavior monitor agent for API response monitoring and its narrowly
    approved evidence catalog.

    The annotated arguments and return type define the data boundary used by callers.
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
    engine = create_engine_from_config(config.db)
    session_factory = make_session_factory(engine)
    resource_catalog = ResourceCatalog(
        lambda: SqlAlchemyResourceCatalogUnitOfWork(session_factory)
    )
    response_value_catalog = ResponseValueCatalog(
        lambda: SqlAlchemyResponseValueCatalogUnitOfWork(session_factory)
    )
    model = ModelSelector.from_config(config.llm).select(
        "api_behavior_monitor"
    )
    resource_tracker = ResourceIdentifierTracker(
        catalog=resource_catalog,
        client=client,
        model=model,
        tracing_runtime=runtime,
    )
    return APIBehaviorMonitorAgent(
        contract_tracker=ResponseContractTracker(),
        resource_identifier_tracker=resource_tracker,
        response_value_tracker=ResponseValueTracker(
            catalog=response_value_catalog,
            client=client,
            model=model,
        ),
        tracing_runtime=runtime,
    )
