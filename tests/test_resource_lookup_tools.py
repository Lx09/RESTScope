"""Public contracts for compact Resource Identifier lookup tools."""

from __future__ import annotations

from pathlib import Path


def _catalog(tmp_path: Path):
    """Create the real SQLite-backed catalog used by the public Capability."""
    from restscope.api_behavior_monitor.resource_identifiers.catalog import ResourceCatalog
    from restscope.db import (
        Base,
        SqlAlchemyResourceCatalogUnitOfWork,
        create_engine_from_url,
        make_session_factory,
    )

    engine = create_engine_from_url(f"sqlite:///{tmp_path / 'resources.sqlite'}")
    Base.metadata.create_all(engine)
    session_factory = make_session_factory(engine)
    return ResourceCatalog(
        lambda: SqlAlchemyResourceCatalogUnitOfWork(session_factory)
    )


def _record_resource(catalog, *, name: str, identifier: str | int) -> None:
    """Record one real resource observation without reaching into SQL state."""
    from restscope.api_behavior_monitor.resource_identifiers.schemas import (
        DetectedResourceGroup,
        MonitoredOperation,
    )

    catalog.record_groups(
        operation=MonitoredOperation(
            operation_key=f"GET /{name}s",
            method="GET",
            path=f"/{name}s",
        ),
        groups=[
            DetectedResourceGroup(
                group_path="$",
                resource_name=name,
                resource_aliases=[name, f"{name}-alias"],
                id_field_name="id",
                id_selector="$.id",
                identifier_values=[identifier],
                classification_source="exact_id",
            )
        ],
    )


def test_resource_list_returns_one_stable_page_of_canonical_names(
    tmp_path: Path,
) -> None:
    """A caller can discover resources without receiving aliases or IDs."""
    from restscope.tools import AgentToolbox
    from restscope.tools.resource import (
        ResourceToolBackend,
        resource_list_resources_tool_spec,
    )
    from restscope.llm import ToolCall

    catalog = _catalog(tmp_path)
    _record_resource(catalog, name="zebra", identifier=9)
    _record_resource(catalog, name="alpha", identifier=1)
    capability = ResourceToolBackend(catalog=catalog)
    toolbox = AgentToolbox()
    toolbox.register(
        spec=resource_list_resources_tool_spec(),
        execute=capability.list_resources,
    )

    result = toolbox.execute(
        ToolCall(
            id="resources",
            name="resource.list_resources",
            arguments={"offset": 0, "limit": 1},
        )
    )

    assert result.status == "succeeded"
    assert result.structured == {
        "resources": [{"name": "alpha"}],
        "total": 2,
        "offset": 0,
        "next_offset": 1,
    }
    assert "alias" not in repr(result.structured)
    assert "identifier" not in repr(result.structured)


def test_resource_id_list_resolves_alias_and_preserves_scalar_type(
    tmp_path: Path,
) -> None:
    """A caller receives a bounded typed-ID page without Monitor internals."""
    from restscope.api_behavior_monitor.resource_identifiers.schemas import (
        DetectedResourceGroup,
        MonitoredOperation,
    )
    from restscope.tools import AgentToolbox
    from restscope.tools.resource import (
        ResourceToolBackend,
        resource_list_ids_tool_spec,
    )
    from restscope.llm import ToolCall

    catalog = _catalog(tmp_path)
    catalog.record_groups(
        operation=MonitoredOperation(
            operation_key="GET /projects",
            method="GET",
            path="/projects",
        ),
        groups=[
            DetectedResourceGroup(
                group_path="$[]",
                resource_name="project",
                resource_aliases=["project", "repository"],
                id_field_name="id",
                id_selector="$[].id",
                identifier_values=[7, "7"],
                classification_source="exact_id",
            )
        ],
    )
    capability = ResourceToolBackend(catalog=catalog)
    toolbox = AgentToolbox()
    toolbox.register(
        spec=resource_list_ids_tool_spec(),
        execute=capability.list_ids,
    )

    result = toolbox.execute(
        ToolCall(
            id="ids",
            name="resource.list_ids",
            arguments={"resource": "repository", "offset": 0, "limit": 1},
        )
    )

    assert result.status == "succeeded"
    assert result.structured == {
        "requested_resource": "repository",
        "status": "found",
        "canonical_resource": "project",
        "ids": [{"value": 7, "value_type": "integer"}],
        "total": 2,
        "offset": 0,
        "next_offset": 1,
    }
    assert "operation" not in repr(result.structured)
    assert "error" not in repr(result.structured)
    assert "last_seen" not in repr(result.structured)


def test_resource_tools_bound_pages_and_treat_unknown_names_as_empty(
    tmp_path: Path,
) -> None:
    """Discovery absence succeeds, while invalid pagination never executes."""
    from restscope.tools import AgentToolbox
    from restscope.tools.resource import (
        ResourceToolBackend,
        resource_list_ids_tool_spec,
        resource_list_resources_tool_spec,
    )
    from restscope.llm import ToolCall

    capability = ResourceToolBackend(catalog=_catalog(tmp_path))
    toolbox = AgentToolbox()
    toolbox.register(
        spec=resource_list_resources_tool_spec(),
        execute=capability.list_resources,
    )
    toolbox.register(
        spec=resource_list_ids_tool_spec(),
        execute=capability.list_ids,
    )

    missing = toolbox.execute(
        ToolCall(
            id="missing",
            name="resource.list_ids",
            arguments={"resource": "unknown"},
        )
    )
    invalid = toolbox.execute(
        ToolCall(
            id="invalid",
            name="resource.list_resources",
            arguments={"limit": 201},
        )
    )

    assert missing.status == "succeeded"
    assert missing.structured == {
        "requested_resource": "unknown",
        "status": "not_found",
        "ids": [],
        "total": 0,
        "offset": 0,
    }
    assert invalid.status == "denied"
    assert invalid.error["code"] == "invalid_tool_arguments"
    assert set(resource_list_resources_tool_spec().input_schema["properties"]) == {
        "offset",
        "limit",
    }
    assert set(resource_list_ids_tool_spec().input_schema["properties"]) == {
        "resource",
        "offset",
        "limit",
    }
