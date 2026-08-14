"""Resolve one Profile completely before constructing the generic Agent.

Callers provide immutable runtime definitions at the composition root. This
Module indexes and validates them once, then hides model and Tool resolution
behind the Harness's registered System Agent Interface.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from threading import Event
from typing import TYPE_CHECKING

from pydantic import BaseModel

from restscope.agent import (
    Agent,
    AgentCompletion,
    AgentProfile,
    AgentProfileRegistry,
    SystemAgentTask,
)
from restscope.agent.prompt import AgentPromptSession, PromptSessionError
from restscope.llm import LLMClient, LLMModelConfig
from restscope.skills import (
    SkillCatalog,
    SkillDefinition,
    SkillPolicy,
    builtin_skill_catalog,
)
from restscope.tools import AgentToolbox, ToolBinding, ToolCatalog, builtin_tool_catalog
from restscope.tools.file import (
    FILE_READ_TOOL_NAME,
    file_read_tool_binding,
)
from restscope.tools.plan import (
    PLAN_READ_TOOL_NAME,
    PLAN_UPDATE_TOOL_NAME,
    AgentPlanStore,
    plan_tool_bindings,
)
from restscope.tools.skill import (
    SKILL_READ_TOOL_NAME,
    skill_read_tool_binding,
)
from restscope.tools.subagent import (
    SUBAGENT_CANCEL_TOOL_NAME,
    SUBAGENT_START_TOOL_NAME,
    SUBAGENT_WAIT_TOOL_NAME,
    SubagentToolCallbacks,
    subagent_tool_bindings,
)

if TYPE_CHECKING:
    from restscope.observability import TracingRuntime

from .agent_control import AgentTreeControl

_SUBAGENT_TOOL_NAMES = (
    SUBAGENT_START_TOOL_NAME,
    SUBAGENT_WAIT_TOOL_NAME,
    SUBAGENT_CANCEL_TOOL_NAME,
)
_PLAN_TOOL_NAMES = (
    PLAN_READ_TOOL_NAME,
    PLAN_UPDATE_TOOL_NAME,
)
_HARNESS_OWNED_TOOL_NAMES = frozenset(
    (
        *_SUBAGENT_TOOL_NAMES,
        *_PLAN_TOOL_NAMES,
        SKILL_READ_TOOL_NAME,
        FILE_READ_TOOL_NAME,
    )
)


@dataclass(frozen=True)
class ToolBindingFactory:
    """Create one session-bound implementation for a Catalog Tool name."""

    name: str
    create: Callable[[], ToolBinding]


@dataclass(frozen=True)
class ContextSourceBinding:
    """Read one named, bounded Markdown source already authorized by the App.

    The owning Adapter must select and safely render model-facing bounded
    Markdown, not a raw response or log stream. The Harness applies redaction
    and verifies the declared length before the Prompt Session adds its fixed
    untrusted-data envelope without re-encoding that Markdown.
    """

    name: str
    read: Callable[[], str]
    max_chars: int = 12_000

    def __post_init__(self) -> None:
        """Reject blank names, non-callable readers, and unsafe size limits."""
        if not self.name.strip():
            raise ValueError("Context Source Binding name must not be blank")
        if not callable(self.read):
            raise TypeError(f"Context Source Binding must be readable: {self.name}")
        if self.max_chars < 1 or self.max_chars > 24_000:
            raise ValueError("Context Source max_chars must be between 1 and 24000")


SystemOutputSchemaBuilder = Callable[[SystemAgentTask], dict[str, object]]
SystemOutputValidator = Callable[[BaseModel, SystemAgentTask], tuple[str, ...]]
SystemTaskAdapter = Callable[[object], SystemAgentTask]


@dataclass(frozen=True)
class SystemAgentDefinition:
    """Bind one Profile to its Harness-owned structured result contract.

    The Profile continues to own all model and capability grants. This
    definition only states which result type a deterministic caller expects and
    how task-local aliases narrow that type for one invocation.
    """

    profile_name: str
    adapt_task: SystemTaskAdapter
    output_model: type[BaseModel]
    build_output_schema: SystemOutputSchemaBuilder
    validate_output: SystemOutputValidator
    output_schema_name: str

    def __post_init__(self) -> None:
        """Reject incomplete result contracts before any Profile can run."""
        if not self.profile_name.strip():
            raise ValueError("System Agent Profile name must not be blank")
        if not self.output_schema_name.strip():
            raise ValueError("System Agent output schema name must not be blank")
        if not issubclass(self.output_model, BaseModel):
            raise TypeError("System Agent output model must be a Pydantic model")
        if not callable(self.adapt_task):
            raise TypeError("System Agent task adapter must be callable")
        if not callable(self.build_output_schema) or not callable(self.validate_output):
            raise TypeError("System Agent result contract callbacks must be callable")


@dataclass(frozen=True)
class AgentRuntimeDefinition:
    """Provide all runtime objects needed to launch configured generic Agents.

    Concrete business Profiles are intentionally supplied by the application,
    never created by this Module. ``skills`` contains additional already-loaded
    caller definitions; installed built-ins are discovered automatically and
    cannot be replaced. Binding factories carry implementations only, while the
    global Catalog remains the sole source of Tool contracts.
    """

    profiles: tuple[AgentProfile, ...]
    models: tuple[LLMModelConfig, ...]
    client: LLMClient
    skills: tuple[SkillDefinition, ...] = ()
    context_sources: tuple[ContextSourceBinding, ...] = ()
    tool_binding_factories: tuple[ToolBindingFactory, ...] = ()
    system_agents: tuple[SystemAgentDefinition, ...] = ()
    max_open_agents: int = 4
    max_active_agents: int = 4
    rollout_budget_weighted_tokens: float = 1_000_000

    def __post_init__(self) -> None:
        """Reject invalid control limits at the composition boundary."""
        if self.max_open_agents < 1:
            raise ValueError("max_open_agents must be greater than zero")
        if self.max_active_agents < 1:
            raise ValueError("max_active_agents must be greater than zero")
        if self.rollout_budget_weighted_tokens <= 0:
            raise ValueError("rollout_budget_weighted_tokens must be greater than zero")


class AgentRuntimeResolver:
    """Validate immutable launch configuration and build authorized Agents."""

    def __init__(
        self,
        definition: AgentRuntimeDefinition,
        *,
        built_in_catalog: ToolCatalog | None = None,
        external_catalog: ToolCatalog | None = None,
        tracing_runtime: TracingRuntime | None = None,
    ) -> None:
        """Index every name and reject bad configuration before model use."""
        self.definition = definition
        self.profiles = AgentProfileRegistry(definition.profiles)
        self.models = _unique_models(definition.models)
        self.built_in_catalog = built_in_catalog or builtin_tool_catalog()
        self.external_catalog = external_catalog or ToolCatalog()
        self.tracing_runtime = tracing_runtime
        self.binding_factories = _unique_binding_factories(
            definition.tool_binding_factories
        )
        # Standard package Skills are always discoverable, but Profile names
        # and dependency grants remain the only authorization mechanism.
        self.skills = SkillCatalog(
            (*builtin_skill_catalog().definitions(), *definition.skills)
        )
        self.skill_policy = SkillPolicy()
        self.context_sources = _unique_context_sources(definition.context_sources)
        self.system_agents = _unique_system_agents(definition.system_agents)
        self._validate()

    def start_system(
        self,
        profile_name: str,
        task: object,
    ) -> tuple[Agent, SystemAgentTask]:
        """Adapt one bounded task and construct its unbounded System root."""
        try:
            definition = self.system_agents[profile_name]
        except KeyError as exc:
            raise ValueError(f"System Agent Profile is not registered: {profile_name}") from exc
        bounded_task = definition.adapt_task(task)
        if not isinstance(bounded_task, SystemAgentTask):
            raise TypeError("System Agent task adapter must return SystemAgentTask")
        schema = definition.build_output_schema(bounded_task)
        if not isinstance(schema, dict):
            raise TypeError("System Agent output schema builder must return an object")
        return (
            self._start_root(
                profile_name=profile_name,
                lifecycle="system",
                rollout_budget_weighted_tokens=None,
                output_model=definition.output_model,
                output_schema=schema,
                output_schema_name=definition.output_schema_name,
                validate_output=lambda output: definition.validate_output(
                    output,
                    bounded_task,
                ),
            ),
            bounded_task,
        )

    def _start_root(
        self,
        *,
        profile_name: str,
        lifecycle: str,
        rollout_budget_weighted_tokens: float | None,
        output_model: type[BaseModel],
        output_schema: dict[str, object],
        output_schema_name: str,
        validate_output: Callable[[BaseModel], tuple[str, ...]],
    ) -> Agent:
        """Build one isolated registered System root behind the launch Interface."""
        from uuid import uuid4

        profile = self.profiles.get(profile_name)
        session_id = f"agent_{uuid4().hex}"
        cancel_event = Event()
        control = AgentTreeControl(
            build_child=self._build_child,
            max_open_agents=self.definition.max_open_agents,
            max_active_agents=self.definition.max_active_agents,
            rollout_budget_weighted_tokens=rollout_budget_weighted_tokens,
        )
        control.register_root(session_id, profile.name, cancel_event)
        return self._build_agent(
            profile_name=profile_name,
            control=control,
            depth=0,
            parent_id=None,
            session_id=session_id,
            cancel_event=cancel_event,
            lifecycle=lifecycle,
            output_model=output_model,
            output_schema=output_schema,
            output_schema_name=output_schema_name,
            validate_output=validate_output,
        )

    def _build_child(
        self,
        profile_name: str,
        control: AgentTreeControl,
        depth: int,
        parent_id: str,
        session_id: str,
        cancel_event,
    ) -> Agent:
        """Construct one short-lived child for the private tree executor."""
        return self._build_agent(
            profile_name=profile_name,
            control=control,
            depth=depth,
            parent_id=parent_id,
            session_id=session_id,
            cancel_event=cancel_event,
            lifecycle="subagent",
            output_model=AgentCompletion,
            output_schema=AgentCompletion.model_json_schema(),
            output_schema_name="AgentCompletion",
            validate_output=lambda _output: (),
        )

    def _build_agent(
        self,
        *,
        profile_name: str,
        control: AgentTreeControl,
        depth: int,
        parent_id: str | None,
        session_id: str,
        cancel_event,
        lifecycle: str,
        output_model: type[BaseModel],
        output_schema: dict[str, object],
        output_schema_name: str,
        validate_output: Callable[[BaseModel], tuple[str, ...]],
    ) -> Agent:
        """Resolve fixed grants and create one root or Subagent instance."""
        profile = self.profiles.get(profile_name)
        selected_skills = self.skills.select(profile.skill_names)
        effective_tool_names = (
            *profile.tool_names,
            *((SKILL_READ_TOOL_NAME,) if selected_skills else ()),
        )
        definitions = [self._tool_definition(name) for name in effective_tool_names]
        special_bindings = {
            binding.name: binding
            for binding in subagent_tool_bindings(
                SubagentToolCallbacks(
                    start=lambda child_profile, objective: control.start_child(
                        owner_id=session_id,
                        allowed_profile_names=profile.subagent_profile_names,
                        profile_name=child_profile,
                        objective=objective,
                    ),
                    wait=lambda child_ids, timeout: control.wait_children(
                        owner_id=session_id,
                        subagent_ids=child_ids,
                        timeout_seconds=timeout,
                    ),
                    cancel=lambda child_id, reason: control.cancel_child(
                        owner_id=session_id,
                        subagent_id=child_id,
                        reason=reason,
                    ),
                )
            )
        }
        # The Store is constructed here rather than by a caller-supplied
        # factory so every root Agent and Subagent receives a private Plan.
        if set(_PLAN_TOOL_NAMES).issubset(profile.tool_names):
            special_bindings.update(
                {
                    binding.name: binding
                    for binding in plan_tool_bindings(AgentPlanStore())
                }
            )
        if selected_skills:
            skill_binding = skill_read_tool_binding(selected_skills)
            special_bindings[skill_binding.name] = skill_binding
        # Unlike skill.read, file.read is an ordinary explicit Profile grant.
        # Its Binding still belongs to the Harness because only the Harness has
        # the final set of selected, policy-checked Skill definitions.
        if FILE_READ_TOOL_NAME in profile.tool_names:
            file_binding = file_read_tool_binding(selected_skills)
            special_bindings[file_binding.name] = file_binding
        bindings: list[ToolBinding] = []
        for name in effective_tool_names:
            if name in special_bindings:
                bindings.append(special_bindings[name])
            else:
                binding = self.binding_factories[name].create()
                if binding.name != name:
                    raise ValueError(
                        f"Tool Binding factory returned the wrong name: {name}"
                    )
                bindings.append(binding)
        toolbox = AgentToolbox.from_catalog(
            catalog=ToolCatalog(definitions),
            selected_names=effective_tool_names,
            bindings=bindings,
            tracing_runtime=self.tracing_runtime,
        )
        model = self.models[profile.model_config_name]
        prompt_session = AgentPromptSession(
            profile=profile,
            skills=selected_skills,
            child_profiles=tuple(
                self.profiles.get(name) for name in profile.subagent_profile_names
            ),
            context_sources=tuple(
                (
                    self.context_sources[name].name,
                    self._safe_context_reader(self.context_sources[name]),
                )
                for name in profile.context_sources
            ),
            model=model,
            tool_specs=toolbox.specs(),
            output_schema=output_schema,
            output_schema_name=output_schema_name,
        )
        return Agent._from_harness(
            profile=profile,
            client=self.definition.client,
            toolbox=toolbox,
            prompt_session=prompt_session,
            session_id=session_id,
            tree_control=control,
            cancel_event=cancel_event,
            lifecycle=lifecycle,
            output_model=output_model,
            validate_output=validate_output,
            depth=depth,
            parent_session_id=parent_id,
        )

    def _safe_context_reader(self, source: ContextSourceBinding) -> Callable[[], str]:
        """Redact and validate one Adapter-rendered Markdown source."""

        def read() -> str:
            value = source.read()
            if not isinstance(value, str):
                raise TypeError(f"Context Source must return text: {source.name}")
            if self.tracing_runtime is None:
                redacted = value
            else:
                redacted = self.tracing_runtime.redactor.redact(value)
            if not isinstance(redacted, str):
                raise TypeError(f"Context Source redaction must return text: {source.name}")
            if len(redacted) > source.max_chars:
                raise PromptSessionError(
                    code="context_budget_exceeded",
                    message=(
                        "Context Source exceeds its bounded Markdown limit: "
                        f"{source.name}"
                    ),
                )
            return redacted

        return read

    def _validate(self) -> None:
        """Reject the complete Profile graph and all unresolved launch names."""
        _reject_profile_cycles(self.profiles)
        _reject_profile_depth(self.profiles)
        for profile_name in self.system_agents:
            self.profiles.get(profile_name)
        built_in_names = {
            definition.name for definition in self.built_in_catalog.definitions()
        }
        external_names = {
            definition.name for definition in self.external_catalog.definitions()
        }
        collisions = built_in_names & external_names
        if collisions:
            raise ValueError(
                "Tool name exists in built-in and external Catalogs: "
                f"{min(collisions)}"
            )
        for binding_name in self.binding_factories:
            if binding_name == SKILL_READ_TOOL_NAME:
                raise ValueError(
                    f"Skill Tool Binding is owned by Harness: {binding_name}"
                )
            if binding_name in _PLAN_TOOL_NAMES:
                raise ValueError(
                    f"Plan Tool Binding is owned by Harness: {binding_name}"
                )
            if binding_name in _SUBAGENT_TOOL_NAMES:
                raise ValueError(
                    f"Subagent Tool Binding is owned by Harness: {binding_name}"
                )
            if binding_name == FILE_READ_TOOL_NAME:
                raise ValueError(
                    f"Skill file Tool Binding is owned by Harness: {binding_name}"
                )
            if binding_name not in built_in_names | external_names:
                raise ValueError(f"Unknown Tool Binding factory: {binding_name}")
        provider_names = set(self.definition.client.registry.list_names())
        for profile in self.profiles.profiles():
            if SKILL_READ_TOOL_NAME in profile.tool_names:
                raise ValueError(
                    "Agent Profiles must not declare skill.read; selecting at "
                    "least one Skill authorizes the Harness loader automatically"
                )
            if FILE_READ_TOOL_NAME in profile.tool_names and not profile.skill_names:
                raise ValueError(
                    "Agent Profiles may grant file.read only with selected Skills"
                )
            selected_plan_tools = tuple(
                name for name in profile.tool_names if name in _PLAN_TOOL_NAMES
            )
            if selected_plan_tools and set(selected_plan_tools) != set(
                _PLAN_TOOL_NAMES
            ):
                raise ValueError(
                    "Agent Profiles must grant both Plan Tools or neither"
                )
            selected_subagent_tools = tuple(
                name for name in profile.tool_names if name in _SUBAGENT_TOOL_NAMES
            )
            if profile.subagent_profile_names and set(selected_subagent_tools) != set(
                _SUBAGENT_TOOL_NAMES
            ):
                raise ValueError(
                    "Agent Profiles with child Profiles must grant all three "
                    "Subagent Tools"
                )
            if selected_subagent_tools and not profile.subagent_profile_names:
                raise ValueError(
                    "Agent Profiles may grant Subagent Tools only with authorized "
                    "child Profiles"
                )
            try:
                model = self.models[profile.model_config_name]
            except KeyError as exc:
                raise ValueError(
                    f"Unknown model configuration in Agent Profile: "
                    f"{profile.model_config_name}"
                ) from exc
            if not model.enabled:
                raise ValueError(
                    f"Agent Profile model configuration is disabled: {model.name}"
                )
            if model.provider not in provider_names:
                raise ValueError(
                    f"Unknown model provider in Agent Profile: {model.provider}"
                )
            for child_name in profile.subagent_profile_names:
                child = self.profiles.get(child_name)
                if child.description is None:
                    raise ValueError(
                        f"Agent child Profile requires a description: {child_name}"
                    )
            granted_tools = set(profile.tool_names)
            granted_context = set(profile.context_sources)
            for skill_name in profile.skill_names:
                try:
                    skill = self.skills.get(skill_name)
                except KeyError as exc:
                    raise ValueError(
                        f"Unknown Skill in Agent Profile: {skill_name}"
                    ) from exc
                missing_tools = set(skill.manifest.required_tools) - granted_tools
                if missing_tools:
                    raise ValueError(
                        f"Skill {skill_name} requires Tool "
                        f"{min(missing_tools)} in the same Profile"
                    )
                missing_context = (
                    set(skill.manifest.required_context_sources) - granted_context
                )
                if missing_context:
                    raise ValueError(
                        f"Skill {skill_name} requires context source "
                        f"{min(missing_context)} in the same Profile"
                    )
                if not self.skill_policy.is_allowed(skill=skill):
                    raise ValueError(f"Skill is not allowed by Harness policy: {skill_name}")
            for source_name in profile.context_sources:
                if source_name not in self.context_sources:
                    raise ValueError(
                        f"Unknown context source in Agent Profile: {source_name}"
                    )
            for tool_name in profile.tool_names:
                self._tool_definition(tool_name)
                if (
                    tool_name not in self.binding_factories
                    and tool_name not in _HARNESS_OWNED_TOOL_NAMES
                ):
                    raise ValueError(f"Missing Tool Binding for Agent Profile: {tool_name}")

    def _tool_definition(self, name: str):
        """Resolve one name from exactly one of the isolated Tool Catalogs."""
        found = []
        for catalog in (self.built_in_catalog, self.external_catalog):
            try:
                found.append(catalog.get(name))
            except KeyError:
                pass
        if not found:
            raise ValueError(f"Unknown Tool in Agent Profile: {name}")
        if len(found) > 1:
            raise ValueError(f"Tool name exists in built-in and external Catalogs: {name}")
        return found[0]


def _unique_models(models: tuple[LLMModelConfig, ...]) -> dict[str, LLMModelConfig]:
    """Index enabled or disabled model configurations without replacement."""
    indexed: dict[str, LLMModelConfig] = {}
    for model in models:
        if model.name in indexed:
            raise ValueError(f"Model configuration is duplicated: {model.name}")
        indexed[model.name] = model
    return indexed


def _unique_binding_factories(
    entries: tuple[ToolBindingFactory, ...],
) -> dict[str, ToolBindingFactory]:
    """Index Tool implementation factories and reject silent replacement."""
    indexed: dict[str, ToolBindingFactory] = {}
    for factory in entries:
        if factory.name in indexed:
            raise ValueError(f"Tool Binding factory is duplicated: {factory.name}")
        if not callable(factory.create):
            raise TypeError(f"Tool Binding factory is not callable: {factory.name}")
        indexed[factory.name] = factory
    return indexed


def _unique_context_sources(
    sources: tuple[ContextSourceBinding, ...],
) -> dict[str, ContextSourceBinding]:
    """Index bounded Context Sources without allowing replacement."""
    indexed: dict[str, ContextSourceBinding] = {}
    for source in sources:
        if source.name in indexed:
            raise ValueError(f"Context Source is duplicated: {source.name}")
        indexed[source.name] = source
    return indexed


def _unique_system_agents(
    definitions: tuple[SystemAgentDefinition, ...],
) -> dict[str, SystemAgentDefinition]:
    """Index System Agent result contracts without silent replacement."""
    indexed: dict[str, SystemAgentDefinition] = {}
    for definition in definitions:
        if definition.profile_name in indexed:
            raise ValueError(
                f"System Agent Profile is duplicated: {definition.profile_name}"
            )
        indexed[definition.profile_name] = definition
    return indexed


def _reject_profile_cycles(registry: AgentProfileRegistry) -> None:
    """Reject self-reference and longer child Profile cycles deterministically."""
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(name: str) -> None:
        if name in visiting:
            raise ValueError(f"Agent Profile child cycle detected at: {name}")
        if name in visited:
            return
        visiting.add(name)
        profile = registry.get(name)
        for child_name in profile.subagent_profile_names:
            registry.get(child_name)
            visit(child_name)
        visiting.remove(name)
        visited.add(name)

    for profile in registry.profiles():
        visit(profile.name)


def _reject_profile_depth(registry: AgentProfileRegistry) -> None:
    """Reject configured paths that can exceed Main depth zero plus three."""

    def longest(name: str) -> int:
        profile = registry.get(name)
        if not profile.subagent_profile_names:
            return 0
        return 1 + max(longest(child) for child in profile.subagent_profile_names)

    for profile in registry.profiles():
        if longest(profile.name) > 3:
            raise ValueError(
                f"Agent Profile graph exceeds maximum Subagent depth: {profile.name}"
            )
