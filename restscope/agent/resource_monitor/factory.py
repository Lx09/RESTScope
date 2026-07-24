"""Configured construction for the Resource Monitor Agent."""

from __future__ import annotations

from restscope.db import (
    SqlAlchemyResourceCatalogUnitOfWork,
    create_engine_from_config,
    make_session_factory,
)
from restscope.llm import LLMClient, ModelSelector, build_llm_client
from restscope.observability import TracingRuntime
from restscope.restscope_config import RESTScopeConfig

from .agent import ResourceMonitorAgent
from .catalog import ResourceCatalog


def build_resource_monitor_agent(
    config: RESTScopeConfig,
    *,
    llm_client: LLMClient | None = None,
    tracing_runtime: TracingRuntime | None = None,
) -> ResourceMonitorAgent:
    """Build Resource Monitor with the App database and configured FAST model."""

    runtime = tracing_runtime
    if runtime is None and llm_client is not None:
        runtime = getattr(llm_client, "tracing_runtime", None)
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
    if hasattr(client, "tracing_runtime"):
        client.tracing_runtime = runtime
    engine = create_engine_from_config(config.db)
    session_factory = make_session_factory(engine)
    catalog = ResourceCatalog(
        lambda: SqlAlchemyResourceCatalogUnitOfWork(session_factory)
    )
    return ResourceMonitorAgent(
        catalog=catalog,
        client=client,
        model=ModelSelector.from_config(config.llm).select("resource_monitor"),
        tracing_runtime=runtime,
    )
