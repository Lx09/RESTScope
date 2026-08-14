"""Derive current resources after a successful observation has committed.

The Resource Response Tracker first reuses immutable identity fields from the
unified Catalog.  For a previously unknown response group it asks the bounded
Resource Identifier System Agent which direct scalar field or fields identify
one persistent instance. A missing operation/resource edge then asks the
Resource State System Agent for one stable semantic result state without
showing it response content. The Catalog atomically stores the edge, recursively
merged instances, current semantic states, and transition events.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from restscope.agent import SystemAgentResult, SystemAgentTask
from restscope.api_behavior_monitor.catalog import (
    APIBehaviorCatalog,
    OperationDefinition,
    ResourceDefinitionRecord,
    ResourceDerivation,
    ResourceDerivationResult,
    normalize_resource_name,
)
from restscope.data_types import JSONValue
from restscope.observability import TracingRuntime

from .resource_identity import (
    RESOURCE_IDENTIFIER_PROFILE_NAME,
    IdentifierCandidateView,
    IdentifierSelectionDecision,
    build_identifier_prompt,
    validate_identifier_decision,
)
from .resource_state import (
    RESOURCE_STATE_PROFILE_NAME,
    ResourceStateDecision,
    build_state_prompt,
    validate_resource_state_output,
)

_MAX_GROUPS = 50
_MAX_INSTANCES = 1000
_MAX_CANDIDATES = 100
_GENERIC_WRAPPERS = frozenset({"data", "items", "results", "collection"})


class SystemAgentRunner(Protocol):
    """Run both registered resource decisions through the Agent Harness."""

    def run_system_agent(
        self,
        profile_name: str,
        task: SystemAgentTask,
    ) -> SystemAgentResult:
        """Return one Harness-validated decision or terminal failure."""

        ...


@dataclass(frozen=True, slots=True)
class _ResponseGroup:
    """Keep one repeated or singular object location and its direct objects."""

    selector: str
    suggested_name: str
    instances: tuple[dict[str, object], ...]


class ResourceResponseTracker:
    """Classify one response into resource types and current instances."""

    def __init__(
        self,
        *,
        catalog: APIBehaviorCatalog,
        system_agent_runner: SystemAgentRunner,
        tracing_runtime: TracingRuntime | None = None,
    ) -> None:
        """Bind durable facts and the Harness-owned synchronous Agent runner."""

        self.catalog = catalog
        self.system_agent_runner = system_agent_runner
        self.tracing_runtime = tracing_runtime or TracingRuntime.disabled()

    def observe(
        self,
        *,
        operation: OperationDefinition,
        observation_id: str,
        body: JSONValue,
    ) -> ResourceDerivationResult:
        """Derive identities and states, then atomically persist final instances.

        Args:
            operation: Matched normalized OpenAPI operation.
            observation_id: Already persisted HTTP Test Case causing the update.
            body: Complete, untruncated 2xx JSON body used only for identities
                and instance snapshots, never for semantic-state prompting.
        """

        groups = _response_groups(body, operation=operation)
        known = self._all_resources()
        identified: list[tuple[str, tuple[str, ...], list[dict[str, object]]]] = []
        for group in groups:
            reusable = _matching_resources(group.instances, known)
            selected = _select_known_resource(group, reusable)
            if selected is None and reusable:
                # More than one established resource meaning fits the same
                # object shape. Do not guess or create a duplicate type.
                continue
            if selected is None:
                identity_fields = self._select_identity_fields(
                    operation=operation,
                    group=group,
                )
                if not identity_fields:
                    # A null Agent answer is an explicit refusal to claim that
                    # this response group establishes a resource identity.
                    continue
                resource_name = normalize_resource_name(group.suggested_name)
            else:
                resource_name = selected.name
                identity_fields = selected.identity_fields
            valid_instances = [
                instance
                for instance in group.instances
                if _has_complete_identity(instance, identity_fields)
            ]
            if not valid_instances:
                continue
            identified.append((resource_name, identity_fields, valid_instances))
        if not identified:
            return ResourceDerivationResult()

        contexts = {
            item.resource_name: item
            for item in self.catalog.read_resource_state_contexts(
                operation_id=operation.operation_id,
                resource_names=tuple(name for name, _fields, _instances in identified),
            )
        }
        selected_states: dict[str, str] = {}
        derivations: list[ResourceDerivation] = []
        for resource_name, identity_fields, instances in identified:
            context = contexts[resource_name]
            result_state = context.operation_result_state
            if result_state is None:
                result_state = selected_states.get(resource_name)
            if result_state is None:
                result_state = self._select_result_state(
                    operation=operation,
                    resource_name=resource_name,
                    existing_states=context.existing_states,
                )
            selected_states[resource_name] = result_state
            derivations.append(
                ResourceDerivation(
                    resource_name=resource_name,
                    identity_fields=list(identity_fields),
                    role=_operation_role(operation.method),
                    result_state=result_state,
                    instances=instances,
                )
            )
        return self.catalog.record_resource_derivations(
            operation_id=operation.operation_id,
            observation_id=observation_id,
            derivations=derivations,
        )

    def _all_resources(self) -> tuple[ResourceDefinitionRecord, ...]:
        """Read all small definition rows without exposing a hidden query limit."""

        output: list[ResourceDefinitionRecord] = []
        offset = 0
        while True:
            page, total = self.catalog.list_resources(offset=offset, limit=200)
            output.extend(page)
            offset += len(page)
            if offset >= total or not page:
                return tuple(output)

    def _select_identity_fields(
        self,
        *,
        operation: OperationDefinition,
        group: _ResponseGroup,
    ) -> tuple[str, ...]:
        """Ask the System Agent for direct scalar identity fields in one group."""

        names = sorted(
            {
                name
                for instance in group.instances
                for name, value in instance.items()
                if _is_identity_scalar(value)
            }
        )
        if not names or len(names) > _MAX_CANDIDATES:
            return ()
        aliases = {f"I{index}": name for index, name in enumerate(names, start=1)}
        candidates = [
            IdentifierCandidateView(
                alias=alias,
                field_path=f"{group.selector}.{name}",
                value_types=tuple(
                    sorted(
                        {
                            "integer"
                            if isinstance(instance.get(name), int)
                            else "string"
                            for instance in group.instances
                            if _is_identity_scalar(instance.get(name))
                        }
                    )
                ),
                observed=True,
            )
            for alias, name in aliases.items()
        ]
        candidate_paths = [operation.path] if "{" in operation.path else []
        prompt = build_identifier_prompt(
            method=operation.method,
            path=operation.path,
            resource_name=group.suggested_name,
            response_location=group.selector,
            candidates=candidates,
            candidate_paths=candidate_paths,
        )
        task = SystemAgentTask(
            objective=prompt.user,
            allowed_result_aliases=prompt.candidate_aliases,
            allowed_result_paths=prompt.candidate_paths,
        )
        with self.tracing_runtime.span(
            "ResourceResponseTracker.select_identity",
            kind="CHAIN",
            input_value={"candidate_count": len(candidates)},
        ) as span:
            result = self.system_agent_runner.run_system_agent(
                RESOURCE_IDENTIFIER_PROFILE_NAME,
                task,
            )
            span.set_output({"status": result.status})
        if result.status != "completed" or result.output is None:
            raise RuntimeError("Resource Identifier System Agent failed")
        decision = IdentifierSelectionDecision.model_validate(result.output)
        issues = validate_identifier_decision(decision, prompt)
        if issues:
            raise ValueError(issues[0])
        if decision.identifier is None:
            return ()
        return tuple(sorted(aliases[alias] for alias in decision.identifier.fields))

    def _select_result_state(
        self,
        *,
        operation: OperationDefinition,
        resource_name: str,
        existing_states: tuple[str, ...],
    ) -> str:
        """Ask the registered no-thinking Profile for a missing edge's state."""

        prompt = build_state_prompt(
            method=operation.method,
            path=operation.path,
            resource_name=resource_name,
            existing_states=existing_states,
        )
        task = SystemAgentTask(
            objective=prompt.user,
            allowed_result_aliases=prompt.existing_states,
        )
        with self.tracing_runtime.span(
            "ResourceResponseTracker.select_state",
            kind="CHAIN",
            input_value={"existing_state_count": len(existing_states)},
        ) as span:
            result = self.system_agent_runner.run_system_agent(
                RESOURCE_STATE_PROFILE_NAME,
                task,
            )
            span.set_output({"status": result.status})
        if result.status != "completed" or result.output is None:
            raise RuntimeError("Resource State System Agent failed")
        decision = ResourceStateDecision.model_validate(result.output)
        issues = validate_resource_state_output(decision, task)
        if issues:
            raise ValueError(issues[0])
        return decision.selected_state


def _response_groups(
    body: JSONValue,
    *,
    operation: OperationDefinition,
) -> tuple[_ResponseGroup, ...]:
    """Discover bounded object and object-array locations recursively."""

    groups: list[_ResponseGroup] = []
    fallback_name = _path_resource_name(operation.path)

    def visit(value: object, selector: str, suggested_name: str) -> None:
        """Collect one object shape, then inspect its nested containers."""

        if len(groups) >= _MAX_GROUPS:
            raise ValueError("response resource groups exceed 50")
        if isinstance(value, dict):
            groups.append(
                _ResponseGroup(
                    selector=selector,
                    suggested_name=suggested_name,
                    instances=(value,),
                )
            )
            for name, child in value.items():
                if isinstance(child, dict):
                    visit(child, f"{selector}.{name}", name)
                elif (
                    isinstance(child, list)
                    and child
                    and all(isinstance(item, dict) for item in child)
                ):
                    if len(child) > _MAX_INSTANCES:
                        raise ValueError("response resource instances exceed 1000")
                    group_selector = f"{selector}.{name}[]"
                    instances = tuple(child)
                    groups.append(
                        _ResponseGroup(
                            selector=group_selector,
                            suggested_name=(
                                fallback_name
                                if name.casefold() in _GENERIC_WRAPPERS
                                else name
                            ),
                            instances=instances,
                        )
                    )
        elif (
            isinstance(value, list)
            and value
            and all(isinstance(item, dict) for item in value)
        ):
            if len(value) > _MAX_INSTANCES:
                raise ValueError("response resource instances exceed 1000")
            groups.append(
                _ResponseGroup(
                    selector="$[]",
                    suggested_name=fallback_name,
                    instances=tuple(value),
                )
            )

    visit(body, "$", fallback_name)
    return tuple(groups)


def _matching_resources(
    instances: tuple[dict[str, object], ...],
    resources: tuple[ResourceDefinitionRecord, ...],
) -> tuple[ResourceDefinitionRecord, ...]:
    """Return established meanings whose complete identity is present."""

    return tuple(
        resource
        for resource in resources
        if all(
            _has_complete_identity(instance, resource.identity_fields)
            for instance in instances
        )
    )


def _select_known_resource(
    group: _ResponseGroup,
    resources: tuple[ResourceDefinitionRecord, ...],
) -> ResourceDefinitionRecord | None:
    """Prefer an exact normalized group name, otherwise require uniqueness."""

    try:
        suggested = normalize_resource_name(group.suggested_name)
    except ValueError:
        suggested = ""
    exact = [item for item in resources if item.name == suggested]
    if len(exact) == 1:
        return exact[0]
    return resources[0] if len(resources) == 1 else None


def _has_complete_identity(
    instance: dict[str, object],
    identity_fields: tuple[str, ...],
) -> bool:
    """Require every immutable direct identity field and an accepted type."""

    return bool(identity_fields) and all(
        name in instance and _is_identity_scalar(instance[name])
        for name in identity_fields
    )


def _is_identity_scalar(value: object) -> bool:
    """Limit durable resource identities to strings and non-Boolean integers."""

    return isinstance(value, str) or (
        isinstance(value, int) and not isinstance(value, bool)
    )


def _operation_role(method: str) -> str:
    """Map HTTP method semantics to the operation-resource edge role."""

    normalized = method.upper()
    if normalized == "POST":
        return "CREATED"
    if normalized == "DELETE":
        return "DELETED"
    if normalized in {"PUT", "PATCH"}:
        return "UPDATED"
    return "REFERENCED"


def _path_resource_name(path: str) -> str:
    """Return the last non-placeholder path segment as an unknown group hint."""

    segments = [
        segment
        for segment in path.split("/")
        if segment and not (segment.startswith("{") and segment.endswith("}"))
    ]
    return segments[-1] if segments else "resource"
