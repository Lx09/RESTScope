"""Resolve one Profile completely before constructing the generic Agent.

Callers provide immutable runtime definitions at the composition root. This
Module indexes and validates them once, then hides model and Tool resolution
behind the Harness's small ``start_main_agent`` Interface.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from restscope.agent import Agent, AgentProfile, AgentProfileRegistry
from restscope.llm import LLMClient, LLMModelConfig
from restscope.skills import SkillDefinition, SkillPolicy, SkillRegistry
from restscope.tools import AgentToolbox, ToolBinding, ToolCatalog, builtin_tool_catalog
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


@dataclass(frozen=True)
class ToolBindingFactory:
    """Create one session-bound implementation for a Catalog Tool name."""

    name: str
    create: Callable[[], ToolBinding]


@dataclass(frozen=True)
class ContextSourceBinding:
    """Read one named, bounded source already authorized by the App.

    ``read`` must return model-facing text, not a raw response or log stream.
    The generic Agent applies a final character boundary before adding it to a
    task, while the source owner remains responsible for domain projection and
    secret removal.
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


@dataclass(frozen=True)
class AgentRuntimeDefinition:
    """Provide all runtime objects needed to launch configured generic Agents.

    Concrete business Profiles are intentionally supplied by the application,
    never created by this Module. Binding factories carry implementations only;
    the global Catalog remains the sole source of Tool contracts.
    """

    profiles: tuple[AgentProfile, ...]
    models: tuple[LLMModelConfig, ...]
    client: LLMClient
    skills: tuple[SkillDefinition, ...] = ()
    context_sources: tuple[ContextSourceBinding, ...] = ()
    tool_binding_factories: tuple[ToolBindingFactory, ...] = ()
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
        tracing_runtime: "TracingRuntime | None" = None,
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
        self.skills = SkillRegistry(definition.skills)
        self.skill_policy = SkillPolicy()
        self.context_sources = _unique_context_sources(definition.context_sources)
        self._validate()

    def start(self, profile_name: str) -> Agent:
        """Construct one Main Agent and its isolated in-memory tree control."""
        profile = self.profiles.get(profile_name)
        from uuid import uuid4

        session_id = f"agent_{uuid4().hex}"
        control = AgentTreeControl(
            build_child=self._build_child,
            max_open_agents=self.definition.max_open_agents,
            max_active_agents=self.definition.max_active_agents,
            rollout_budget_weighted_tokens=(
                self.definition.rollout_budget_weighted_tokens
            ),
        )
        control.register_main(session_id, profile.name)
        return self._build_agent(
            profile_name=profile_name,
            control=control,
            depth=0,
            parent_id=None,
            session_id=session_id,
            cancel_event=None,
            is_subagent=False,
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
            is_subagent=True,
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
        is_subagent: bool,
    ) -> Agent:
        """Resolve fixed grants and create one Main or Subagent instance."""
        profile = self.profiles.get(profile_name)
        definitions = [self._tool_definition(name) for name in profile.tool_names]
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
        bindings: list[ToolBinding] = []
        for name in profile.tool_names:
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
            selected_names=profile.tool_names,
            bindings=bindings,
            tracing_runtime=self.tracing_runtime,
        )
        return Agent._from_harness(
            profile=profile,
            client=self.definition.client,
            model=self.models[profile.model_config_name],
            toolbox=toolbox,
            skill_instructions=tuple(
                skill.instructions for skill in self.skills.select(profile.skill_names)
            ),
            context_sources=tuple(
                (
                    self.context_sources[name].name,
                    self._safe_context_reader(self.context_sources[name]),
                    self.context_sources[name].max_chars,
                )
                for name in profile.context_sources
            ),
            session_id=session_id,
            tree_control=control,
            cancel_event=cancel_event,
            is_subagent=is_subagent,
            depth=depth,
            parent_session_id=parent_id,
        )

    def _safe_context_reader(self, source: ContextSourceBinding) -> Callable[[], str]:
        """Redact one authorized source before the Agent applies its size limit."""

        def read() -> str:
            value = source.read()
            if not isinstance(value, str):
                raise TypeError(f"Context Source must return text: {source.name}")
            if self.tracing_runtime is None:
                return value
            redacted = self.tracing_runtime.redactor.redact(value)
            if not isinstance(redacted, str):
                raise TypeError(f"Context Source redaction must return text: {source.name}")
            return redacted

        return read

    def _validate(self) -> None:
        """Reject the complete Profile graph and all unresolved launch names."""
        _reject_profile_cycles(self.profiles)
        _reject_profile_depth(self.profiles)
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
                f"{sorted(collisions)[0]}"
            )
        for binding_name in self.binding_factories:
            if binding_name in _SUBAGENT_TOOL_NAMES:
                raise ValueError(
                    f"Subagent Tool Binding is owned by Harness: {binding_name}"
                )
            if binding_name not in built_in_names | external_names:
                raise ValueError(f"Unknown Tool Binding factory: {binding_name}")
        provider_names = set(self.definition.client.registry.list_names())
        for profile in self.profiles.profiles():
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
                    f"Agent Profile model configuration is disabled: {model.role}"
                )
            if model.provider not in provider_names:
                raise ValueError(
                    f"Unknown model provider in Agent Profile: {model.provider}"
                )
            for child_name in profile.subagent_profile_names:
                self.profiles.get(child_name)
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
                        f"{sorted(missing_tools)[0]} in the same Profile"
                    )
                missing_context = (
                    set(skill.manifest.required_context_sources) - granted_context
                )
                if missing_context:
                    raise ValueError(
                        f"Skill {skill_name} requires context source "
                        f"{sorted(missing_context)[0]} in the same Profile"
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
                if tool_name not in self.binding_factories and not tool_name.startswith("subagent."):
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
        if model.role in indexed:
            raise ValueError(f"Model configuration is duplicated: {model.role}")
        indexed[model.role] = model
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
