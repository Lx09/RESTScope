"""Model-facing Resource Tool scenarios for complete Identifier Records."""

from __future__ import annotations

from pathlib import Path


def _catalog(tmp_path: Path):
    """Create one isolated unified Response Monitor Catalog."""
    from restscope.api_behavior_monitor.catalog import APIBehaviorCatalog
    from restscope.db import (
        Base,
        SqlAlchemyAPIBehaviorUnitOfWork,
        create_engine_from_url,
        make_session_factory,
    )

    engine = create_engine_from_url(f"sqlite:///{tmp_path / 'tools.sqlite'}")
    Base.metadata.create_all(engine)
    sessions = make_session_factory(engine)
    return APIBehaviorCatalog(lambda: SqlAlchemyAPIBehaviorUnitOfWork(sessions))


def _record_resource(catalog, *, name: str = "assignment") -> None:
    """Record one real composite observation through the Catalog Interface."""
    from restscope.api_behavior_monitor.catalog import (
        OperationDefinition,
        ResourceDerivation,
    )

    catalog.ensure_operation(
        OperationDefinition(
            operation_id="GET /assignments",
            method="GET",
            path="/assignments",
        )
    )
    catalog.record_resource_derivations(
        operation_id="GET /assignments",
        derivations=[
            ResourceDerivation(
                resource_name=name,
                identity_fields=["employee_id", "project_id"],
                role="REFERENCED",
                instances=[{"employee_id": "e1", "project_id": 7}],
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
    from restscope.tools.resource import (
        ResourceToolBackend,
        resource_list_ids_tool_spec,
    )

    catalog = _catalog(tmp_path)
    _record_resource(catalog)

    result = ResourceToolBackend(catalog=catalog).list_ids(resource="assignment")

    assert result["structured"]["ids"] == [
        {
            "identifier": '{"employee_id":"e1","project_id":7}',
            "components": [
                {"name": "employee_id", "value": "e1", "value_type": "string"},
                {"name": "project_id", "value": 7, "value_type": "integer"},
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


def test_resource_id_list_uses_exact_lookup_without_scanning_pages(
    tmp_path: Path,
) -> None:
    """Identifier lookup asks the Catalog for one name instead of paging all names."""
    from restscope.tools.resource import ResourceToolBackend

    catalog = _catalog(tmp_path)
    _record_resource(catalog)

    # If the identifier path still scans resource pages, this replacement
    # turns that unnecessary work into an immediately visible test failure.
    catalog.list_resources = lambda **_arguments: (_ for _ in ()).throw(  # type: ignore[method-assign]
        AssertionError("identifier lookup must not scan resource pages")
    )
    result = ResourceToolBackend(catalog=catalog).list_ids(resource="assignment")

    assert result["structured"]["status"] == "found"
