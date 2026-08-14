"""Build and release the private production resources behind one App.

The module receives validated configuration and constructs the database,
Behavior Monitor, Request Generation, Target API Client, Harness, tracing, and
optional observer. It returns one private owner used by ``runtime.py`` for
target binding, Main-Agent creation, and ordered shutdown.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from threading import RLock

from sqlalchemy import Engine

from restscope.agent import SystemAgentResult, SystemAgentTask
from restscope.api_behavior_monitor import (
    APIBehaviorResponseProcessor,
    build_api_behavior_monitor_coordinator,
)
from restscope.api_behavior_monitor.catalog import (
    APIBehaviorCatalog,
    OperationDefinition,
)
from restscope.config import RESTScopeConfig
from restscope.db import (
    SqlAlchemyAPIBehaviorUnitOfWork,
    create_engine_from_config,
    make_session_factory,
)
from restscope.db.bootstrap import _FreshSQLiteDatabase, prepare_fresh_sqlite
from restscope.harness import ContextSourceBinding, HarnessRuntime, build_harness
from restscope.harness.operation_testing import OperationTestingService
from restscope.harness.test_progress import (
    TEST_PROGRESS_CONTEXT_SOURCE,
    TestProgressContextReader,
)
from restscope.observability import (
    LiveRunObserver,
    Redactor,
    TracingRuntime,
    build_tracing_runtime,
    configure_logging,
)
from restscope.openapi_parser import OpenAPISpecIR, build_openapi_document
from restscope.orchestration import OrchestrationRuntime
from restscope.request_generation import (
    BehaviorMonitorReferences,
    RequestGenerationConfigStore,
    RequestGenerationPatchRuntime,
    SeededRandom,
)
from restscope.target_api import TargetAPIClient
from restscope.tools.context import ToolContext
from restscope.tools.database import DatabaseQueryToolBackend
from restscope.tools.resource import ResourceToolBackend
from restscope.tools.test_case import TestCaseQueryToolBackend
from restscope.ui import UIService, start_ui_service

from .profiles import _build_agent_runtime_definition


class _DeferredSystemAgentRunner:
    """Bind the Monitor to its final Harness System Agent entrypoint once."""

    def __init__(self) -> None:
        """Create an unbound App-private System Agent runner."""
        self._lock = RLock()
        self._run: Callable[[str, SystemAgentTask], SystemAgentResult] | None = None

    def bind(self, run: Callable[[str, SystemAgentTask], SystemAgentResult]) -> None:
        """Install the production runner exactly once.

        Args:
            run: Final Harness entrypoint for registered System Agents.

        Raises:
            TypeError: ``run`` cannot be called.
            RuntimeError: A final Harness was already bound.
        """
        if not callable(run):
            raise TypeError("System Agent runner must be callable")
        with self._lock:
            if self._run is not None:
                raise RuntimeError("System Agent runner is already bound")
            self._run = run

    def run_system_agent(
        self,
        profile_name: str,
        task: SystemAgentTask,
    ) -> SystemAgentResult:
        """Forward one synchronous decision after composition completes.

        The Monitor can hold this runner while the circular production graph is
        being assembled. Calls before the Harness binding fail safely instead
        of bypassing Harness Profile and result validation.
        """
        with self._lock:
            run = self._run
        if run is None:
            raise RuntimeError("System Agent runner is not initialized")
        return run(profile_name, task)


@dataclass(frozen=True)
class _AppResources:
    """Own every long-lived production implementation used by one App.

    ``RESTScopeApp`` delegates target publication, Orchestration execution,
    optional observer discovery, and shutdown here. This keeps the public lifecycle free
    of database, Monitor, Request Generation, and Harness navigation details.
    """

    harness: HarnessRuntime
    tracing: TracingRuntime
    catalog: APIBehaviorCatalog
    generation_store: RequestGenerationConfigStore
    database_engine: Engine
    run_observer: LiveRunObserver | None = None
    ui_service: UIService | None = None

    @property
    def ui_url(self) -> str | None:
        """Return the active optional observer URL."""
        return self.ui_service.url if self.ui_service is not None else None

    def bind_target(self, context: ToolContext) -> None:
        """Publish one parsed API to persistence, generation, then the Harness.

        Args:
            context: Validated target and parsed OpenAPI operations.

        The Harness Context is published last. A Catalog or generation failure
        therefore cannot expose a partially initialized target to Tools.
        """
        ir = context.ir
        self.catalog.initialize_api(
            document=build_openapi_document(ir, list(ir.operations)),
            operations=[
                OperationDefinition(
                    operation_id=operation.operation_key,
                    method=operation.method,
                    path=operation.path,
                    description=operation.description,
                )
                for operation in ir.operations.values()
            ],
        )
        self.generation_store.initialize_once(ir)
        self.harness.bind_context(context)

    def run_orchestration(self, focus: str | None = None) -> None:
        """Run the App-lifetime Ledger loop through fresh System Agent roots.

        Args:
            focus: Optional user emphasis appended to RESTScope's fixed Goal.

        The public App intentionally keeps returning ``None``; final evidence
        remains available through existing observability and audit surfaces.
        """
        OrchestrationRuntime(
            self.harness,
            observe=(
                self.run_observer.record_orchestration
                if self.run_observer is not None
                else None
            ),
        ).run(focus)

    def close(self) -> None:
        """Attempt every resource cleanup in dependency order."""
        steps: list[Callable[[], None]] = [
            self.harness.close_agents,
            self.harness.clear_context,
        ]
        if self.harness.mcp_host is not None:
            steps.append(self.harness.mcp_host.close)
        if self.ui_service is not None:
            steps.append(self.ui_service.close)
        if self.run_observer is not None:
            steps.append(self.run_observer.close)
        steps.extend((self.database_engine.dispose, self.tracing.close))
        _close_in_order(steps)


def _compose_app_resources(config: RESTScopeConfig) -> _AppResources:
    """Construct the complete production graph with failure cleanup.

    Args:
        config: Validated process configuration supplied to ``RESTScopeApp``.

    Returns:
        The private owner for the fully connected production graph.

    Raises:
        BaseException: Any construction step failed. Created UI, observer,
            tracing, database, and Harness resources are closed before the
            original error is re-raised.
    """
    database: _FreshSQLiteDatabase | None = None
    harness: HarnessRuntime | None = None
    tracing: TracingRuntime | None = None
    run_observer: LiveRunObserver | None = None
    ui_service: UIService | None = None
    database_engine: Engine | None = None
    try:
        config = _resolve_app_random_seed(config)
        config, database = _prepare_app_database(config)
        configure_logging(config.logging, log_file=config.log_file)
        tracing = _build_app_tracing_runtime(config)
        tracing.redactor.register_secrets(
            (
                config.llm.thinking.api_key,
                config.llm.fast.api_key,
                config.tracing.api_key,
            )
        )
        if config.ui.enabled:
            run_observer = LiveRunObserver(redactor=tracing.redactor)
            tracing.bind_run_observer(run_observer)
            ui_service = start_ui_service(observer=run_observer, port=config.ui.port)
            if ui_service is None:
                run_observer.close()
                tracing.bind_run_observer(None)
                run_observer = None

        database_engine = create_engine_from_config(config.db)
        session_factory = make_session_factory(database_engine)
        generation_store = RequestGenerationConfigStore()
        catalog = APIBehaviorCatalog(
            lambda: SqlAlchemyAPIBehaviorUnitOfWork(session_factory)
        )
        system_agent_runner = _DeferredSystemAgentRunner()
        coordinator = build_api_behavior_monitor_coordinator(
            config,
            catalog=catalog,
            system_agent_runner=system_agent_runner,
            tracing_runtime=tracing,
        )
        references = BehaviorMonitorReferences(catalog)
        target_api_client = TargetAPIClient(
            response_processor=APIBehaviorResponseProcessor(coordinator),
            run_observer=run_observer,
        )
        operation_testing_service = OperationTestingService(
            config_store=generation_store,
            target_api_client=target_api_client,
            tracing_runtime=tracing,
            reference_values=references,
            api_behavior_catalog=catalog,
        )

        def current_ir() -> OpenAPISpecIR:
            """Return initialized IR after the production Harness exists."""
            if harness is None:
                raise RuntimeError("App Harness is not initialized")
            return harness.require_context().ir

        patch_runtime = RequestGenerationPatchRuntime(
            store=generation_store,
            ir_provider=current_ir,
            references=references,
        )
        test_progress_reader = TestProgressContextReader(catalog)
        harness = build_harness(
            tracing_runtime=tracing,
            target_api_client=target_api_client,
            observed_response_reader=catalog,
            resource_tool_backend=ResourceToolBackend(catalog=catalog),
            database_query_backend=DatabaseQueryToolBackend(engine=database_engine),
            request_generation_patch_runtime=patch_runtime,
            operation_testing_service=operation_testing_service,
            test_case_query_backend=TestCaseQueryToolBackend(catalog=catalog),
            agent_runtime=_build_agent_runtime_definition(
                config,
                tracing_runtime=tracing,
                test_progress_context=ContextSourceBinding(
                    name=TEST_PROGRESS_CONTEXT_SOURCE,
                    read=test_progress_reader.read,
                ),
            ),
        )
        system_agent_runner.bind(harness.run_system_agent)
        return _AppResources(
            harness=harness,
            tracing=tracing,
            catalog=catalog,
            generation_store=generation_store,
            database_engine=database_engine,
            run_observer=run_observer,
            ui_service=ui_service,
        )
    except BaseException:
        cleanup_steps: list[Callable[[], None]] = []
        if harness is not None and harness.mcp_host is not None:
            cleanup_steps.append(harness.mcp_host.close)
        if ui_service is not None:
            cleanup_steps.append(ui_service.close)
        if run_observer is not None:
            cleanup_steps.append(run_observer.close)
        if tracing is not None:
            cleanup_steps.append(tracing.close)
        if database_engine is not None:
            cleanup_steps.append(database_engine.dispose)
        if database is not None:
            cleanup_steps.append(database.cleanup)
        try:
            _close_in_order(cleanup_steps)
        except BaseException:  # noqa: BLE001, S110
            pass
        raise


def _build_app_tracing_runtime(config: RESTScopeConfig) -> TracingRuntime:
    """Build tracing with configured App secrets registered for redaction."""
    return build_tracing_runtime(
        config.tracing,
        redactor=Redactor(
            (
                config.llm.thinking.api_key,
                config.llm.fast.api_key,
                config.tracing.api_key,
            )
        ),
    )


def _prepare_app_database(
    config: RESTScopeConfig,
) -> tuple[RESTScopeConfig, _FreshSQLiteDatabase]:
    """Prepare the one-shot database and return normalized configuration."""
    db_config, database = prepare_fresh_sqlite(config.db)
    return replace(config, db=db_config), database


def _resolve_app_random_seed(config: RESTScopeConfig) -> RESTScopeConfig:
    """Resolve the optional root random seed once during composition."""
    if config.random.seed is not None:
        return config
    source = SeededRandom()
    return replace(config, random=replace(config.random, seed=source.seed))


def _close_in_order(steps: list[Callable[[], None]]) -> None:
    """Attempt ordered cleanup steps and re-raise the first failure afterward."""
    first_error: BaseException | None = None
    for step in steps:
        try:
            step()
        except BaseException as exc:  # noqa: BLE001
            if first_error is None:
                first_error = exc
    if first_error is not None:
        raise first_error
