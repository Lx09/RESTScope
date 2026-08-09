"""Compose RESTScope and run its one blocking App-lifetime Main Agent.

``RESTScopeApp`` prepares deterministic services, binds one parsed OpenAPI
target during :meth:`initialize`, and starts the generic Profile-authorized
Main Agent during :meth:`start`. The Main loop receives no public task or
result DTO: its stable Profile instructions define the mission, and the call
blocks until the Agent completes or a safe runtime failure is raised.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from types import TracebackType
from typing import Annotated, Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter
from sqlalchemy import Engine

from restscope.agent import Agent, AgentProfile
from restscope.api_behavior_monitor import (
    APIBehaviorResponseProcessor,
    build_api_behavior_monitor_coordinator,
)
from restscope.api_behavior_monitor.resource_identifiers import ResourceCatalog
from restscope.api_behavior_monitor.response_values import ResponseValueCatalog
from restscope.operation_smoke import (
    BehaviorMonitorReferenceValues,
    OperationSmokeCoordinator,
    SmokeBatchRunner,
    build_operation_smoke_coordinator,
)
from restscope.harness import (
    AgentRuntimeDefinition,
    HarnessRuntime,
    build_harness,
)
from restscope.llm import ModelSelector, build_llm_client
from restscope.tools.context import ToolContext, ToolContextError
from restscope.tools.resource import ResourceToolBackend
from restscope.target_http import TargetHTTPTransport
from restscope.openapi_audit import OpenAPIAudit, OpenAPIChangeEventRecord
from restscope.openapi_parser import OpenAPIParser, build_openapi_document
from restscope.observability import (
    LiveRunObserver,
    Redactor,
    TracingRuntime,
    build_tracing_runtime,
    configure_logging,
)
from restscope.request_generation import SeededRandom
from restscope.config import RESTScopeConfig
from restscope.db import (
    SqlAlchemyGeneratorConfigUnitOfWork,
    SqlAlchemyOpenAPIUnitOfWork,
    SqlAlchemyResourceCatalogUnitOfWork,
    SqlAlchemyResponseValueCatalogUnitOfWork,
    SqlAlchemySmokeMemoryUnitOfWork,
    create_engine_from_config,
    make_session_factory,
)
from restscope.db.bootstrap import _FreshSQLiteDatabase, prepare_fresh_sqlite
from restscope.request_generation import RequestGenerationConfigStore
from restscope.harness.operation_testing import OperationTestingService
from restscope.tools.plan import PLAN_READ_TOOL_NAME, PLAN_UPDATE_TOOL_NAME


_MAIN_PROFILE_INSTRUCTIONS = """You are RESTScope's single long-lived Main Agent.

- Work on the API target already initialized for this App lifetime. Treat these
  Profile instructions as the continuing mission; there is no separate task
  request or user-authored objective at startup.
- Own every semantic workflow decision. Decide what to investigate, which
  authorized Skills to load, which Tools or Subagents to use, what order to
  follow, whether another attempt is useful, and when to finish.
- Inspect authorized Skill metadata and load the Skills relevant to the current
  work. Skills provide methods; they do not grant access or override this
  Profile or the Harness contract.
- Initialize the private Plan for the current work and revise it as evidence
  changes. The Plan is working memory, not evidence, a scheduler, or persistent
  state.
- Use a child Profile only when its described capability fits a bounded piece
  of the work. Supply a complete objective and required evidence because the
  child receives no parent conversation or hidden state.
- Base factual conclusions on current authorized Tool or Subagent results.
  Never invent evidence references or treat a plan, prior belief, Skill text,
  OpenAPI description, or successful Tool execution as proof of an API outcome.
- Do not repeat an action unless new evidence, changed state, or a specific
  predicted benefit makes the next attempt materially different.
- Finish when the current authorized capabilities cannot make meaningful safe
  progress. Report unsupported, blocked, safety-skipped, and unresolved work
  explicitly.
- Return only the required bounded AgentCompletion result.
"""


class _SchemaSourceModel(BaseModel):
    """Reject unknown fields in one App initialization source."""

    model_config = ConfigDict(extra="forbid")


class _FileSchemaSource(_SchemaSourceModel):
    """Read an OpenAPI document from one local filesystem path."""

    kind: Literal["file"]
    path: str


class _UrlSchemaSource(_SchemaSourceModel):
    """Read an OpenAPI document from one explicit URL."""

    kind: Literal["url"]
    url: str


class _InlineSchemaSource(_SchemaSourceModel):
    """Parse an OpenAPI document supplied directly by the App caller."""

    kind: Literal["inline"]
    format: Literal["yaml", "json"] = "yaml"
    content: str


_SchemaSource = Annotated[
    _FileSchemaSource | _UrlSchemaSource | _InlineSchemaSource,
    Field(discriminator="kind"),
]


class RESTScopeApp:
    """Own all long-lived services used by one RESTScope process.

    Creating the app is intentionally more than constructing a data object.  It
    opens the App-lifetime database, tracing exporter, behavior monitor, HTTP
    transport, testing service, shared Tool implementations, and deterministic
    Agent Harness.
    The app also records which resources it created so :meth:`close` can release
    them if startup succeeds *or* fails part-way through.
    """

    def __init__(
        self,
        *,
        config: RESTScopeConfig,
        operation_smoke_coordinator: OperationSmokeCoordinator | None = None,
        harness_runtime: HarnessRuntime | Any | None = None,
        tracing_runtime: TracingRuntime | None = None,
    ) -> None:
        """Build runtime collaborators, or adopt explicitly injected test doubles.

        ``harness_runtime`` and ``operation_smoke_coordinator`` are injection
        points used by tests and embedders.  In the normal path both are
        omitted and RESTScope wires the complete production stack itself.
        """

        # Keep local ownership markers until construction completes.  If any
        # later constructor raises, the exception handler can close only the
        # resources opened by this method and leave injected objects untouched.
        database: _FreshSQLiteDatabase | None = None
        built_runtime: HarnessRuntime | Any | None = None
        built_tracing_runtime = tracing_runtime is None
        trace_runtime: TracingRuntime | None = None
        run_observer: LiveRunObserver | None = None
        ui_service: Any | None = None
        operation_testing_service: OperationTestingService | None = None
        api_behavior_monitor_coordinator = None
        target_transport: TargetHTTPTransport | None = None
        openapi_audit: OpenAPIAudit | None = None
        database_engine: Engine | None = None
        try:
            config = _resolve_app_random_seed(config)
            # A default runtime needs a private, freshly migrated SQLite file.
            # An injected runtime owns its own persistence and must not be
            # silently paired with another database.
            if harness_runtime is None:
                config, database = _prepare_app_database(config)

            self.config = config
            configure_logging(config.logging, log_file=config.log_file)
            self.random_source = SeededRandom(config.random.seed)
            smoke_coordinator = operation_smoke_coordinator
            trace_runtime = (
                _build_app_tracing_runtime(config)
                if tracing_runtime is None
                else tracing_runtime
            )
            self._tracing_runtime = trace_runtime
            self._tracing_runtime.redactor.register_secrets(
                (
                    config.llm.thinking.api_key,
                    config.llm.fast.api_key,
                    config.tracing.api_key,
                )
            )
            if config.ui.enabled:
                run_observer = LiveRunObserver(
                    redactor=self._tracing_runtime.redactor
                )
                self._tracing_runtime.bind_run_observer(run_observer)
            if harness_runtime is None:
                # The Store holds only current per-input Generators. The
                # behavior monitor supplies observed identifiers/response values to
                # generators, while the transport sends requests and returns
                # every response to that monitor.
                database_engine = create_engine_from_config(config.db)
                session_factory = make_session_factory(database_engine)
                request_generation_store = RequestGenerationConfigStore(
                    lambda: SqlAlchemyGeneratorConfigUnitOfWork(session_factory)
                )
                resource_catalog = ResourceCatalog(
                    lambda: SqlAlchemyResourceCatalogUnitOfWork(session_factory)
                )
                response_value_catalog = ResponseValueCatalog(
                    lambda: SqlAlchemyResponseValueCatalogUnitOfWork(
                        session_factory
                    )
                )
                openapi_audit = OpenAPIAudit(
                    lambda: SqlAlchemyOpenAPIUnitOfWork(session_factory)
                )
                api_behavior_monitor_coordinator = build_api_behavior_monitor_coordinator(
                    config,
                    resource_catalog=resource_catalog,
                    response_value_catalog=response_value_catalog,
                    openapi_audit=openapi_audit,
                    tracing_runtime=self._tracing_runtime,
                )
                reference_values = BehaviorMonitorReferenceValues(
                    api_behavior_monitor_coordinator
                )
                resource_backend = ResourceToolBackend(
                    catalog=resource_catalog
                )
                observed_response_fields_provider = (
                    response_value_catalog.list_observed_response_fields
                )
                target_transport = TargetHTTPTransport(
                    response_processor=APIBehaviorResponseProcessor(
                        api_behavior_monitor_coordinator
                    ),
                    run_observer=run_observer,
                )
                operation_testing_service = OperationTestingService(
                    config_store=request_generation_store,
                    transport=target_transport,
                    tracing_runtime=self._tracing_runtime,
                    reference_values=reference_values,
                )
                # The Harness exposes HTTP and evidence lookup Tools.
                # Generated Batch execution stays internal: Smoke receives the
                # testing service through its narrow runner Protocol below.
                main_runtime = _build_main_agent_runtime_definition(
                    config,
                    tracing_runtime=self._tracing_runtime,
                )
                built_runtime = build_harness(
                    tracing_runtime=self._tracing_runtime,
                    target_http_transport=target_transport,
                    observed_response_fields_provider=(
                        observed_response_fields_provider
                    ),
                    resource_tool_backend=resource_backend,
                    agent_runtime=main_runtime,
                )
                harness_runtime = built_runtime
                if smoke_coordinator is None:
                    smoke_coordinator = build_operation_smoke_coordinator(
                        config,
                        config_store=request_generation_store,
                        # OperationTestingService implements this structural
                        # Protocol. The cast records that composition-root
                        # binding without making the testing layer import its
                        # coordinating workflow.
                        batch_runner=cast(
                            SmokeBatchRunner,
                            operation_testing_service,
                        ),
                        reference_values=reference_values,
                        http_tool=built_runtime.target_http_tool,
                        context_provider=built_runtime.require_context,
                        openapi_backend=built_runtime.openapi_backend,
                        resource_backend=resource_backend,
                        unit_of_work_factory=(
                            lambda: SqlAlchemySmokeMemoryUnitOfWork(
                                session_factory
                            )
                        ),
                        llm_client=(main_runtime.client if main_runtime else None),
                        tracing_runtime=self._tracing_runtime,
                    )
            elif smoke_coordinator is None:
                # Mixing a custom Harness with a default Smoke Coordinator would
                # connect unrelated runtime state, so require callers
                # to inject the matching Coordinator explicitly.
                raise ValueError(
                    "A custom Harness runtime requires an injected "
                    "OperationSmokeCoordinator"
                )
            self.operation_smoke_coordinator = smoke_coordinator
            self.harness_runtime = harness_runtime
            self.operation_testing_service = (
                operation_testing_service if harness_runtime is built_runtime else None
            )
            self.api_behavior_monitor_coordinator = (
                api_behavior_monitor_coordinator
                if harness_runtime is built_runtime
                else None
            )
            self.target_http_transport = (
                target_transport if harness_runtime is built_runtime else None
            )
            self.openapi_audit: OpenAPIAudit | None = (
                openapi_audit if harness_runtime is built_runtime else None
            )
            self._database_engine = (
                database_engine if harness_runtime is built_runtime else None
            )
            bind_tracing_runtime = getattr(
                self.harness_runtime,
                "bind_tracing_runtime",
                None,
            )
            if callable(bind_tracing_runtime):
                bind_tracing_runtime(self._tracing_runtime)
            _bind_run_observer(
                target_transport=self.target_http_transport,
                testing_service=self.operation_testing_service,
                observer=run_observer,
            )
            if run_observer is not None:
                from restscope.ui import start_ui_service

                ui_service = start_ui_service(
                    observer=run_observer,
                    port=config.ui.port,
                )
                if ui_service is None:
                    run_observer.close()
                    self._tracing_runtime.bind_run_observer(None)
                    _bind_run_observer(
                        target_transport=self.target_http_transport,
                        testing_service=self.operation_testing_service,
                        observer=None,
                    )
                    run_observer = None
            self._run_observer = run_observer
            self._ui_service = ui_service
            self._main_agent: Agent | None = None
            self._main_loop_started = False
            self._closed = False
        except BaseException:
            # Startup is transactional from the caller's perspective: a failed
            # constructor must not leave an MCP host, exporter, or temporary
            # database running in the background.
            _close_runtime_host(built_runtime)
            if ui_service is not None:
                ui_service.close()
            if run_observer is not None:
                run_observer.close()
            if built_tracing_runtime and trace_runtime is not None:
                trace_runtime.close()
            if database_engine is not None:
                database_engine.dispose()
            if database is not None:
                database.cleanup()
            raise

    @classmethod
    def from_environment(
        cls,
        *,
        env_file: str | Path | None = None,
        operation_smoke_coordinator: OperationSmokeCoordinator | None = None,
        harness_runtime: HarnessRuntime | Any | None = None,
        tracing_runtime: TracingRuntime | None = None,
    ) -> "RESTScopeApp":
        """Load `.env`/environment config and build the program runtime."""

        config = RESTScopeConfig.from_environment(Path(env_file).expanduser() if env_file else None)
        return cls.from_config(
            config,
            operation_smoke_coordinator=operation_smoke_coordinator,
            harness_runtime=harness_runtime,
            tracing_runtime=tracing_runtime,
        )

    @classmethod
    def from_config(
        cls,
        config: RESTScopeConfig,
        *,
        operation_smoke_coordinator: OperationSmokeCoordinator | None = None,
        harness_runtime: HarnessRuntime | Any | None = None,
        tracing_runtime: TracingRuntime | None = None,
    ) -> "RESTScopeApp":
        """Build RESTScope through the same composition path as direct use."""

        return cls(
            config=config,
            operation_smoke_coordinator=operation_smoke_coordinator,
            harness_runtime=harness_runtime,
            tracing_runtime=tracing_runtime,
        )

    @property
    def tool_context(self) -> ToolContext | None:
        """Return the initialized target snapshot, or ``None`` before startup."""
        require_context = getattr(self.harness_runtime, "require_context", None)
        if not callable(require_context):
            return None
        try:
            return require_context()
        except ToolContextError as exc:
            if exc.code == "tool_context_not_initialized":
                return None
            raise

    @property
    def tracing_runtime(self) -> TracingRuntime:
        """Return the App-owned tracing runtime until the App is closed."""
        return self._tracing_runtime

    @property
    def ui_url(self) -> str | None:
        """Return the active loopback observer URL, or ``None`` when disabled."""
        service = self._ui_service
        return service.url if service is not None else None

    def initialize(
        self,
        *,
        schema_source: Mapping[str, Any],
        base_url: str | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> ToolContext:
        """Parse and bind one immutable-by-convention target snapshot to this App."""

        self._ensure_open()
        if self.tool_context is not None:
            raise ToolContextError(
                "tool_context_already_initialized",
                "Tool context is already initialized",
            )

        source = TypeAdapter(_SchemaSource).validate_python(dict(schema_source))
        ir = OpenAPIParser.parse(_schema_source_value(source))
        parser_errors = [
            *ir.diagnostics.spec_errors,
            *ir.diagnostics.path_errors,
            *ir.diagnostics.operation_errors,
        ]
        if parser_errors:
            first = parser_errors[0]
            raise ValueError(f"OpenAPI parsing produced {len(parser_errors)} error(s): {first.message}")
        if not ir.operations:
            raise ValueError("OpenAPI schema contains no testable operations")

        context = ToolContext(
            ir=ir,
            baseline_schema_source=source.model_dump(mode="json"),
            base_url=base_url,
            headers=headers or {},
        )
        testing_service = self.operation_testing_service
        if testing_service is not None:
            if self.openapi_audit is not None:
                self.openapi_audit.initialize(
                    build_openapi_document(ir, list(ir.operations))
                )
            testing_service.config_store.initialize_once(ir)
        self.harness_runtime.bind_context(context)
        return context

    def export_current_openapi(self) -> dict[str, Any]:
        """Return the normalized OpenAPI document persisted for audit/export.

        This method does not restore an App or expose raw schema-source paths.
        It is available only on the default database-backed runtime after
        :meth:`initialize` has bound the current API.
        """

        self._ensure_open()
        if self.openapi_audit is None:
            raise RuntimeError("The current runtime has no OpenAPI Audit")
        return self.openapi_audit.current_document()

    def list_openapi_change_events(
        self,
        operation_key: str | None = None,
    ) -> list[OpenAPIChangeEventRecord]:
        """Return chronological persisted response changes for inspection."""

        self._ensure_open()
        if self.openapi_audit is None:
            raise RuntimeError("The current runtime has no OpenAPI Audit")
        return self.openapi_audit.list_changes(operation_key)

    def start(self) -> None:
        """Start the Main Agent once and block until its model loop finishes.

        The target must already be initialized. This call intentionally takes
        no task and returns no result: the ``main`` Profile contains the stable
        App-lifetime mission, while ``AgentCompletion`` remains an internal
        loop-termination protocol. Cancellation, budget exhaustion, prompt
        capacity failures, and other terminal runtime failures are raised.
        """

        self._ensure_open()
        if self.tool_context is None:
            raise ToolContextError(
                "tool_context_not_initialized",
                "Tool context is not initialized",
            )
        if self._main_loop_started:
            raise RuntimeError("RESTScope Main Agent loop has already started")
        self._main_loop_started = True
        marker = {"profile_name": "main", "mode": "blocking"}
        if self._run_observer is not None:
            self._run_observer.begin_run(marker)
        try:
            with self.tracing_runtime.span(
                "RESTScopeApp.start",
                kind="CHAIN",
                input_value=marker,
            ) as span:
                self._main_agent = self.harness_runtime.start_main_agent("main")
                self._main_agent.start()
                terminal = {"profile_name": "main", "status": "completed"}
                span.set_output(terminal)
                span.set_attribute("restscope.agent.status", "completed")
        except KeyboardInterrupt:
            if self._run_observer is not None:
                self._run_observer.interrupt_run()
            raise
        except BaseException as exc:
            if self._run_observer is not None:
                self._run_observer.end_run(error=exc)
            raise
        if self._run_observer is not None:
            self._run_observer.end_run(terminal)

    def close(self) -> None:
        """Close owned runtime resources."""

        if self._closed:
            return
        close_main_agent = getattr(self.harness_runtime, "close_main_agent", None)
        if callable(close_main_agent):
            close_main_agent()
        clear_context = getattr(self.harness_runtime, "clear_context", None)
        if callable(clear_context):
            clear_context()
        clear_smoke_state = getattr(
            self.operation_smoke_coordinator,
            "clear_app_state",
            None,
        )
        if callable(clear_smoke_state):
            try:
                clear_smoke_state()
            except Exception:
                # Resource cleanup must continue even if a custom injected
                # Smoke Coordinator cannot release its optional in-memory state.
                pass
        mcp_host = getattr(self.harness_runtime, "mcp_host", None)
        try:
            if mcp_host is not None:
                mcp_host.close()
        finally:
            try:
                if self._ui_service is not None:
                    self._ui_service.close()
                if self._run_observer is not None:
                    self._run_observer.close()
            finally:
                try:
                    if self._database_engine is not None:
                        self._database_engine.dispose()
                finally:
                    self.tracing_runtime.close()
                    self._closed = True

    def __enter__(self) -> "RESTScopeApp":
        self._ensure_open()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        del exc_type, exc, tb
        self.close()

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("RESTScopeApp is already closed")


def _schema_source_value(source: Any) -> str:
    if source.kind == "file":
        return source.path
    if source.kind == "url":
        return source.url
    return source.content


def _build_app_tracing_runtime(config: RESTScopeConfig) -> TracingRuntime:
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


def _build_main_agent_runtime_definition(
    config: RESTScopeConfig,
    *,
    tracing_runtime: TracingRuntime,
) -> AgentRuntimeDefinition | None:
    """Compose the currently runnable, intentionally capability-light Main.

    An unset thinking model keeps configuration-only and parser tests usable,
    but :meth:`RESTScopeApp.start` then fails closed through the Harness's
    existing ``agent_runtime_not_configured`` error. Once configured, the Main
    receives only its private Plan pair. Testing Skills, OpenAPI discovery,
    domain Tools, Context Sources, and child Profiles remain empty until their
    individual contracts are approved and implemented.
    """
    model = ModelSelector.from_config(config.llm).thinking
    if not model.enabled:
        return None
    return AgentRuntimeDefinition(
        profiles=(
            AgentProfile(
                name="main",
                instructions=_MAIN_PROFILE_INSTRUCTIONS,
                model_config_name="thinking",
                tool_names=(PLAN_READ_TOOL_NAME, PLAN_UPDATE_TOOL_NAME),
            ),
        ),
        models=(model,),
        client=build_llm_client(config.llm, tracing_runtime=tracing_runtime),
    )


def _prepare_app_database(
    config: RESTScopeConfig,
) -> tuple[RESTScopeConfig, _FreshSQLiteDatabase]:
    """Prepare the default runtime's one-shot database and normalize its config."""

    db_config, database = prepare_fresh_sqlite(config.db)
    return replace(config, db=db_config), database


def _resolve_app_random_seed(config: RESTScopeConfig) -> RESTScopeConfig:
    """Resolve the optional root seed once before building App collaborators."""
    if config.random.seed is not None:
        return config
    source = SeededRandom()
    return replace(
        config,
        random=replace(config.random, seed=source.seed),
    )


def _close_runtime_host(runtime: Any | None) -> None:
    if runtime is None:
        return
    host = getattr(runtime, "mcp_host", None)
    if host is not None:
        try:
            host.close()
        except Exception:
            pass


def _bind_run_observer(
    *,
    target_transport: TargetHTTPTransport | None,
    testing_service: OperationTestingService | None,
    observer: LiveRunObserver | None,
) -> None:
    """Attach live observation to the App-owned target transports."""
    if target_transport is not None:
        target_transport.run_observer = observer
    if testing_service is not None:
        testing_service.transport.run_observer = observer
