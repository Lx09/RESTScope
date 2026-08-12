"""Build and own the private resources behind one ``RESTScopeApp``.

The module receives App configuration plus optional caller-built Harness and
tracing runtimes. It returns one private resource collection used by
``runtime.py`` for target binding, Main-Agent execution, audit reads, and
shutdown. Keeping the production object graph here lets App callers understand
the lifecycle without learning database, Monitor, HTTP, Tool, or Agent wiring.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from threading import RLock

from sqlalchemy import Engine

from restscope.agent import Agent, AgentProfile, SystemAgentResult, SystemAgentTask
from restscope.api_behavior_monitor import (
    APIBehaviorResponseProcessor,
    build_api_behavior_monitor_coordinator,
)
from restscope.api_behavior_monitor.catalog import (
    APIBehaviorCatalog,
    OpenAPIChangeEventRecord,
    OperationDefinition,
)
from restscope.api_behavior_monitor.resource_identity import (
    IDENTIFIER_SYSTEM_AGENT_INSTRUCTIONS,
    IdentifierSelectionDecision,
    RESOURCE_IDENTIFIER_PROFILE_NAME,
    identifier_system_output_schema,
    validate_identifier_system_output,
)
from restscope.config import RESTScopeConfig
from restscope.db import (
    SqlAlchemyAPIBehaviorUnitOfWork,
    create_engine_from_config,
    make_session_factory,
)
from restscope.db.bootstrap import _FreshSQLiteDatabase, prepare_fresh_sqlite
from restscope.harness import (
    AgentRuntimeDefinition,
    HarnessRuntime,
    SystemAgentDefinition,
    build_harness,
)
from restscope.harness.operation_testing import OperationTestingService
from restscope.llm import build_llm_client, build_llm_model_config
from restscope.observability import (
    LiveRunObserver,
    Redactor,
    TracingRuntime,
    build_tracing_runtime,
    configure_logging,
)
from restscope.openapi_parser import OpenAPISpecIR, build_openapi_document
from restscope.request_generation import (
    BehaviorMonitorReferences,
    RequestGenerationConfigStore,
    RequestGenerationPatchRuntime,
    SeededRandom,
)
from restscope.target_api import TargetAPIClient
from restscope.tools.context import ToolContext, ToolContextError
from restscope.tools.plan import PLAN_READ_TOOL_NAME, PLAN_UPDATE_TOOL_NAME
from restscope.tools.resource import ResourceToolBackend
from restscope.ui import UIService, start_ui_service


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


class _DeferredSystemAgentRunner:
    """Break the Monitor/Harness construction cycle with one bind-once Adapter.

    The Monitor is built before the final Harness exists. The App binds the
    Harness's System Agent entrypoint before any target response can arrive, so
    an early call fails safely instead of invoking a model outside the Harness.
    """

    def __init__(self) -> None:
        """Create an unbound App-private System Agent runner."""
        self._lock = RLock()
        self._run: Callable[[str, SystemAgentTask], SystemAgentResult] | None = None

    def bind(
        self,
        run: Callable[[str, SystemAgentTask], SystemAgentResult],
    ) -> None:
        """Install the sole production runner exactly once.

        Args:
            run: Harness method that starts a registered System Agent.

        Raises:
            TypeError: The supplied value cannot be called.
            RuntimeError: A runner was already installed.
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

        Args:
            profile_name: Registered System Agent Profile to start.
            task: Bounded Profile-specific task input.

        Returns:
            The validated structured System Agent result.

        Raises:
            RuntimeError: Composition has not bound the Harness yet.
        """
        with self._lock:
            run = self._run
        if run is None:
            raise RuntimeError("System Agent runner is not initialized")
        return run(profile_name, task)


@dataclass(frozen=True)
class _AppResources:
    """Own every long-lived implementation used by one App.

    The App runtime delegates mechanical binding, audit reads, Main Agent
    creation, and ordered shutdown here. Optional fields distinguish the
    database-backed default composition from an adopted caller-built Harness;
    callers never need to understand that difference.
    """

    config: RESTScopeConfig
    harness: HarnessRuntime
    tracing: TracingRuntime
    run_observer: LiveRunObserver | None = None
    ui_service: UIService | None = None
    catalog: APIBehaviorCatalog | None = None
    generation_store: RequestGenerationConfigStore | None = None
    database_engine: Engine | None = None

    @property
    def tool_context(self) -> ToolContext | None:
        """Return the bound target Context, or ``None`` before initialization."""
        try:
            return self.harness.require_context()
        except ToolContextError as exc:
            if exc.code == "tool_context_not_initialized":
                return None
            raise

    @property
    def ui_url(self) -> str | None:
        """Return the active optional observer URL."""
        return self.ui_service.url if self.ui_service is not None else None

    def bind_target(self, context: ToolContext) -> None:
        """Publish one parsed API to persistence, generation, then the Harness.

        Args:
            context: Validated target URL, headers, source snapshot, and parsed
                OpenAPI intermediate representation.

        The Harness Context is published last. A Catalog or Generation failure
        therefore cannot expose a partially initialized target to Tools.
        """
        ir = context.ir
        if self.catalog is not None:
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
        if self.generation_store is not None:
            self.generation_store.initialize_once(ir)
        self.harness.bind_context(context)

    def current_openapi(self) -> dict[str, object]:
        """Return the current persisted normalized OpenAPI document.

        Raises:
            RuntimeError: The adopted Harness has no API Behavior Catalog.
        """
        if self.catalog is None:
            raise RuntimeError("The current runtime has no API Behavior Catalog")
        return self.catalog.current_openapi()

    def list_openapi_changes(
        self,
        operation_key: str | None,
    ) -> list[OpenAPIChangeEventRecord]:
        """Return persisted response-contract changes in chronological order."""
        if self.catalog is None:
            raise RuntimeError("The current runtime has no API Behavior Catalog")
        return self.catalog.list_openapi_changes(operation_key)

    def start_main_agent(self) -> Agent:
        """Create the App's sole long-lived Main Agent through the Harness."""
        return self.harness.start_main_agent("main")

    def close(self) -> None:
        """Release App-owned runtime resources in dependency order.

        Main and System Agents stop before their Context and MCP Host disappear.
        Optional UI and observation close before database and tracing backends.
        Every step is attempted even when an earlier close operation raises.
        """
        steps: list[Callable[[], None]] = [
            self.harness.close_main_agent,
            self.harness.clear_context,
        ]
        mcp_host = self.harness.mcp_host
        if mcp_host is not None:
            steps.append(mcp_host.close)
        if self.ui_service is not None:
            steps.append(self.ui_service.close)
        if self.run_observer is not None:
            steps.append(self.run_observer.close)
        if self.database_engine is not None:
            steps.append(self.database_engine.dispose)
        steps.append(self.tracing.close)
        _close_in_order(steps)


def _compose_app_resources(
    config: RESTScopeConfig,
    *,
    harness_runtime: HarnessRuntime | None,
    tracing_runtime: TracingRuntime | None,
) -> _AppResources:
    """Build default resources or adopt a caller-created concrete Harness.

    Args:
        config: Validated App configuration.
        harness_runtime: Optional real Harness owned by the caller before this
            call and adopted by the App after successful construction.
        tracing_runtime: Optional tracing runtime. A failed construction does
            not close this caller-provided object.

    Returns:
        The complete private resource collection and normalized configuration.

    Raises:
        BaseException: Any startup error after first releasing resources opened
            by this function and deleting an incomplete fresh database.
    """
    database: _FreshSQLiteDatabase | None = None
    built_harness: HarnessRuntime | None = None
    built_tracing = tracing_runtime is None
    tracing: TracingRuntime | None = None
    run_observer: LiveRunObserver | None = None
    ui_service: UIService | None = None
    database_engine: Engine | None = None

    try:
        config = _resolve_app_random_seed(config)
        if harness_runtime is None:
            config, database = _prepare_app_database(config)

        configure_logging(config.logging, log_file=config.log_file)
        tracing = (
            _build_app_tracing_runtime(config)
            if tracing_runtime is None
            else tracing_runtime
        )
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

        catalog: APIBehaviorCatalog | None = None
        generation_store: RequestGenerationConfigStore | None = None
        if harness_runtime is None:
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

            # Patch validation needs the future Harness Context. The closure is
            # invoked only after build_harness has assigned built_harness.
            def current_ir() -> OpenAPISpecIR:
                """Return initialized IR after the default Harness exists."""
                if built_harness is None:
                    raise RuntimeError("App Harness is not initialized")
                return built_harness.require_context().ir

            patch_runtime = RequestGenerationPatchRuntime(
                store=generation_store,
                ir_provider=current_ir,
                references=references,
            )
            agent_runtime = _build_main_agent_runtime_definition(
                config,
                tracing_runtime=tracing,
            )
            built_harness = build_harness(
                tracing_runtime=tracing,
                target_api_client=target_api_client,
                observed_response_reader=catalog,
                resource_tool_backend=ResourceToolBackend(catalog=catalog),
                request_generation_patch_runtime=patch_runtime,
                operation_testing_service=operation_testing_service,
                agent_runtime=agent_runtime,
            )
            system_agent_runner.bind(built_harness.run_system_agent)
            harness_runtime = built_harness

        # Both paths now have one concrete Harness. Bind tracing only after the
        # whole object graph has been constructed successfully.
        if harness_runtime is None:
            raise RuntimeError("App Harness construction produced no runtime")
        harness_runtime.bind_tracing_runtime(tracing)
        return _AppResources(
            config=config,
            harness=harness_runtime,
            tracing=tracing,
            run_observer=run_observer,
            ui_service=ui_service,
            catalog=catalog,
            generation_store=generation_store,
            database_engine=database_engine,
        )
    except BaseException:
        # Each cleanup attempt is independent: a failing optional UI must not
        # leave a fresh database or an App-created tracing backend behind.
        cleanup_steps: list[Callable[[], None]] = []
        if built_harness is not None and built_harness.mcp_host is not None:
            cleanup_steps.append(built_harness.mcp_host.close)
        if ui_service is not None:
            cleanup_steps.append(ui_service.close)
        if run_observer is not None:
            cleanup_steps.append(run_observer.close)
            if tracing is not None:
                cleanup_steps.append(lambda: tracing.bind_run_observer(None))
        if built_tracing and tracing is not None:
            cleanup_steps.append(tracing.close)
        if database_engine is not None:
            cleanup_steps.append(database_engine.dispose)
        if database is not None:
            cleanup_steps.append(database.cleanup)
        try:
            _close_in_order(cleanup_steps)
        except BaseException:
            # The construction error explains why no App exists. Cleanup
            # failures must not replace that primary error at the public seam.
            pass
        raise


def _build_app_tracing_runtime(config: RESTScopeConfig) -> TracingRuntime:
    """Build tracing with every configured App secret registered for redaction."""
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
    """Compose the App-authorized Main and Monitor System Agent Profiles."""
    thinking = build_llm_model_config("thinking", config.llm.thinking)
    fast = build_llm_model_config("fast", config.llm.fast)
    profiles: list[AgentProfile] = []
    system_agents: list[SystemAgentDefinition] = []
    models = []
    if thinking.enabled:
        models.append(thinking)
        profiles.append(
            AgentProfile(
                name="main",
                instructions=_MAIN_PROFILE_INSTRUCTIONS,
                model_config_name="thinking",
                tool_names=(PLAN_READ_TOOL_NAME, PLAN_UPDATE_TOOL_NAME),
            )
        )
    if fast.enabled:
        models.append(fast)
        profiles.append(
            AgentProfile(
                name=RESOURCE_IDENTIFIER_PROFILE_NAME,
                instructions=IDENTIFIER_SYSTEM_AGENT_INSTRUCTIONS,
                model_config_name="fast",
            )
        )
        system_agents.append(
            SystemAgentDefinition(
                profile_name=RESOURCE_IDENTIFIER_PROFILE_NAME,
                adapt_task=SystemAgentTask.model_validate,
                output_model=IdentifierSelectionDecision,
                build_output_schema=identifier_system_output_schema,
                validate_output=validate_identifier_system_output,
                output_schema_name="IdentifierSelectionDecision",
            )
        )
    if not profiles:
        return None
    return AgentRuntimeDefinition(
        profiles=tuple(profiles),
        models=tuple(models),
        client=build_llm_client(config.llm, tracing_runtime=tracing_runtime),
        system_agents=tuple(system_agents),
    )


def _prepare_app_database(
    config: RESTScopeConfig,
) -> tuple[RESTScopeConfig, _FreshSQLiteDatabase]:
    """Prepare the default runtime's one-shot database and normalized config."""
    db_config, database = prepare_fresh_sqlite(config.db)
    return replace(config, db=db_config), database


def _resolve_app_random_seed(config: RESTScopeConfig) -> RESTScopeConfig:
    """Resolve the optional root random seed once during App composition."""
    if config.random.seed is not None:
        return config
    source = SeededRandom()
    return replace(config, random=replace(config.random, seed=source.seed))


def _close_in_order(steps: list[Callable[[], None]]) -> None:
    """Attempt ordered cleanup steps and re-raise the first failure afterward.

    Args:
        steps: Already ordered zero-argument cleanup operations.

    Raises:
        BaseException: The first cleanup failure, after every later step has
            still been attempted.
    """
    first_error: BaseException | None = None
    for step in steps:
        try:
            step()
        except BaseException as exc:
            if first_error is None:
                first_error = exc
    if first_error is not None:
        raise first_error
