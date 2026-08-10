"""Model-facing Resource Tool scenarios for complete Identifier Records."""

from __future__ import annotations

from pathlib import Path


def _catalog(tmp_path: Path):
    """Create one isolated real Resource Catalog."""
    from restscope.api_behavior_monitor.resource_identifiers.catalog import ResourceCatalog
    from restscope.db import Base, SqlAlchemyResourceCatalogUnitOfWork, create_engine_from_url, make_session_factory

    engine = create_engine_from_url(f"sqlite:///{tmp_path / 'tools.sqlite'}")
    Base.metadata.create_all(engine)
    sessions = make_session_factory(engine)
    return ResourceCatalog(lambda: SqlAlchemyResourceCatalogUnitOfWork(sessions))


def _record_resource(catalog, *, name: str = "assignment") -> None:
    """Record one real composite observation through the Catalog Interface."""
    from restscope.api_behavior_monitor.resource_identifiers.schemas import DetectedResourceGroup, MonitoredOperation

    catalog.record_groups(
        operation=MonitoredOperation(operation_key="GET /assignments", method="GET", path="/assignments"),
        groups=[
            DetectedResourceGroup.model_validate(
                {
                    "group_path": "$[]",
                    "resource_name": name,
                    "resource_aliases": [name, "allocation"],
                    "identifier_name": "employeeId/projectId",
                    "identifier_path": "/assignments/{employeeId}/{projectId}",
                    "identifier_fields": [
                        {"component": "employeeId", "field_name": "employee_id", "selector": "$[].employee_id"},
                        {"component": "projectId", "field_name": "project_id", "selector": "$[].project_id"},
                    ],
                    "identifier_records": [
                        {
                            "components": [
                                {"name": "employeeId", "value": "e1", "value_type": "string"},
                                {"name": "projectId", "value": 7, "value_type": "integer"},
                            ]
                        }
                    ],
                    "classification_source": "llm",
                }
            )
        ],
    )


def test_resource_list_returns_canonical_names(tmp_path: Path) -> None:
    """Resource discovery remains a small paginated name-only Tool."""
    from restscope.tools.resource import ResourceToolBackend

    catalog = _catalog(tmp_path)
    _record_resource(catalog)

    result = ResourceToolBackend(catalog=catalog).list_resources()

    assert result == {"structured": {"resources": [{"name": "assignment"}], "total": 1, "offset": 0}}


def test_resource_id_list_returns_definition_and_ordered_components(tmp_path: Path) -> None:
    """The Tool exposes complete records and no legacy scalar ID fields."""
    from restscope.tools.resource import ResourceToolBackend, resource_list_ids_tool_spec

    catalog = _catalog(tmp_path)
    _record_resource(catalog)

    result = ResourceToolBackend(catalog=catalog).list_ids(resource="allocation")

    assert result["structured"]["ids"] == [
        {
            "identifier": "employeeId/projectId",
            "components": [
                {"name": "employeeId", "value": "e1", "value_type": "string"},
                {"name": "projectId", "value": 7, "value_type": "integer"},
            ],
        }
    ]
    item_schema = resource_list_ids_tool_spec().output_schema["properties"]["ids"]["items"]
    assert set(item_schema["properties"]) == {"identifier", "components"}


def test_resource_id_list_reports_unknown_alias_without_failure(tmp_path: Path) -> None:
    """An unknown resource is a successful empty discovery result."""
    from restscope.tools.resource import ResourceToolBackend

    result = ResourceToolBackend(catalog=_catalog(tmp_path)).list_ids(resource="missing")

    assert result["structured"] == {
        "requested_resource": "missing",
        "status": "not_found",
        "ids": [],
        "total": 0,
        "offset": 0,
    }
