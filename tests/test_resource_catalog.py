"""Catalog scenarios for Identifier Definitions and complete Records."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path


def _catalog(tmp_path: Path):
    """Create one isolated real Catalog and engine."""
    from restscope.api_behavior_monitor.resource_identifiers.catalog import ResourceCatalog
    from restscope.db import Base, SqlAlchemyResourceCatalogUnitOfWork, create_engine_from_url, make_session_factory

    engine = create_engine_from_url(f"sqlite:///{tmp_path / 'resources.sqlite'}")
    Base.metadata.create_all(engine)
    sessions = make_session_factory(engine)
    return ResourceCatalog(lambda: SqlAlchemyResourceCatalogUnitOfWork(sessions)), engine


def _group(*, records: list[tuple[str, int]], aliases: list[str] | None = None):
    """Build one two-component assignment Identifier observation."""
    from restscope.api_behavior_monitor.resource_identifiers.schemas import DetectedResourceGroup

    return DetectedResourceGroup.model_validate(
        {
            "group_path": "$[]",
            "resource_name": "assignment",
            "resource_aliases": aliases or ["assignment"],
            "identifier_name": "employeeId/projectId",
            "identifier_path": "/assignments/{employeeId}/{projectId}",
            "identifier_fields": [
                {"component": "employeeId", "field_name": "employee_id", "selector": "$[].employee_id"},
                {"component": "projectId", "field_name": "project_id", "selector": "$[].project_id"},
            ],
            "identifier_records": [
                {
                    "components": [
                        {"name": "employeeId", "value": employee, "value_type": "string"},
                        {"name": "projectId", "value": project, "value_type": "integer"},
                    ]
                }
                for employee, project in records
            ],
            "classification_source": "llm",
        }
    )


def _operation():
    """Return the stable operation identity used by Catalog scenarios."""
    from restscope.api_behavior_monitor.resource_identifiers.schemas import MonitoredOperation

    return MonitoredOperation(
        operation_key="GET /assignments",
        method="GET",
        path="/assignments",
    )


def test_current_baseline_adds_the_definition_table(tmp_path: Path) -> None:
    """A fresh database contains seven Resource Identifier tables and downgrades cleanly."""
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import inspect
    from restscope.db import create_engine_from_url
    from restscope.db.migrations import MIGRATIONS_DIR

    database = tmp_path / "migration.sqlite"
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database}")
    command.upgrade(config, "head")
    engine = create_engine_from_url(f"sqlite:///{database}")
    expected = {
        "resources", "resource_aliases", "resource_identifier_definitions",
        "operation_resource_rules", "resource_identifiers",
        "resource_operation_usages", "resource_monitor_errors",
    }
    assert expected <= set(inspect(engine).get_table_names())
    command.downgrade(config, "base")
    assert not expected & set(inspect(engine).get_table_names())


def test_catalog_preserves_typed_row_wise_composite_records(tmp_path: Path) -> None:
    """Lookup returns complete tuples and updates recency without duplicating them."""
    from restscope.api_behavior_monitor import ResourceLookupRequest

    catalog, _engine = _catalog(tmp_path)
    first = datetime(2026, 8, 10, 10, tzinfo=timezone.utc)
    later = first + timedelta(minutes=5)
    catalog.record_groups(
        operation=_operation(),
        groups=[_group(records=[("e1", 10), ("e2", 20)], aliases=["assignment", "allocation"])],
        observed_at=first,
    )
    catalog.record_groups(
        operation=_operation(),
        groups=[_group(records=[("e1", 10)])],
        observed_at=later,
    )

    result = catalog.lookup(ResourceLookupRequest(resource="ALLOCATION"))

    assert result.canonical_resource == "assignment"
    assert result.total == 2
    assert result.identifiers[0].last_seen_at == later
    assert [
        [(component.name, component.value_type, component.value) for component in record.components]
        for record in result.identifiers
    ] == [
        [("employeeId", "string", "e1"), ("projectId", "integer", 10)],
        [("employeeId", "string", "e2"), ("projectId", "integer", 20)],
    ]


def test_learned_rule_round_trips_ordered_fields(tmp_path: Path) -> None:
    """The deterministic reuse rule retains path, definition, and component order."""
    catalog, _engine = _catalog(tmp_path)
    catalog.record_groups(operation=_operation(), groups=[_group(records=[("e1", 10)])])

    rule = catalog.list_rules(_operation())[0]

    assert rule.identifier_name == "employeeId/projectId"
    assert rule.identifier_path == "/assignments/{employeeId}/{projectId}"
    assert [item.component for item in rule.identifier_fields] == ["employeeId", "projectId"]


def test_catalog_rolls_back_when_a_definition_changes_shape(tmp_path: Path) -> None:
    """A conflicting component order cannot partially publish new aliases or Records."""
    import pytest
    from restscope.api_behavior_monitor import ResourceLookupRequest
    from restscope.db.adapters.resource_catalog import ResourceCatalogConflict

    catalog, _engine = _catalog(tmp_path)
    catalog.record_groups(operation=_operation(), groups=[_group(records=[("e1", 10)])])
    conflict = _group(records=[("e2", 20)], aliases=["assignment", "new-alias"])
    conflict.identifier_fields.reverse()

    with pytest.raises(ResourceCatalogConflict):
        catalog.record_groups(operation=_operation(), groups=[conflict])

    result = catalog.lookup(ResourceLookupRequest(resource="assignment"))
    assert result.total == 1
    assert "new-alias" not in result.aliases
