from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path


def _catalog(tmp_path: Path):
    from restscope.agent.api_behavior_monitor import ResourceCatalog
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


def _catalog_with_engine(tmp_path: Path):
    from restscope.agent.api_behavior_monitor import ResourceCatalog
    from restscope.db import (
        Base,
        SqlAlchemyResourceCatalogUnitOfWork,
        create_engine_from_url,
        make_session_factory,
    )

    engine = create_engine_from_url(f"sqlite:///{tmp_path / 'resources.sqlite'}")
    Base.metadata.create_all(engine)
    session_factory = make_session_factory(engine)
    return (
        ResourceCatalog(lambda: SqlAlchemyResourceCatalogUnitOfWork(session_factory)),
        engine,
    )


def test_resource_catalog_migration_adds_and_removes_six_tables(tmp_path: Path) -> None:
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import inspect

    from restscope.db import create_engine_from_url
    from restscope.db.migrations import MIGRATIONS_DIR

    database = tmp_path / "migration.sqlite"
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database}")

    command.upgrade(config, "0002_create_generator_configs")
    command.upgrade(config, "head")

    engine = create_engine_from_url(f"sqlite:///{database}")
    table_names = set(inspect(engine).get_table_names())
    assert {
        "resources",
        "resource_aliases",
        "operation_resource_rules",
        "resource_identifiers",
        "resource_operation_usages",
        "resource_monitor_errors",
    } <= table_names

    command.downgrade(config, "0002_create_generator_configs")
    assert not {
        "resources",
        "resource_aliases",
        "operation_resource_rules",
        "resource_identifiers",
        "resource_operation_usages",
        "resource_monitor_errors",
    } & set(inspect(engine).get_table_names())


def test_catalog_records_aliases_typed_ids_and_latest_operation_usage(
    tmp_path: Path,
) -> None:
    from restscope.agent.api_behavior_monitor import (
        DetectedResourceGroup,
        MonitoredOperation,
        ResourceLookupRequest,
    )

    catalog = _catalog(tmp_path)
    first_seen = datetime(2026, 7, 23, 10, tzinfo=timezone.utc)
    later_seen = first_seen + timedelta(minutes=5)
    operation = MonitoredOperation(
        operation_key="POST /users",
        method="POST",
        path="/users",
    )
    group = DetectedResourceGroup(
        group_path="$",
        resource_name="user",
        resource_aliases=["user", "owner"],
        id_field_name="id",
        id_selector="$.id",
        identifier_values=[42, "42"],
        classification_source="exact_id",
    )

    catalog.record_groups(operation=operation, groups=[group], observed_at=first_seen)
    catalog.record_groups(
        operation=operation,
        groups=[
            group.model_copy(
                update={"resource_aliases": ["user", "account"], "identifier_values": [42]}
            )
        ],
        observed_at=later_seen,
    )

    result = catalog.lookup(ResourceLookupRequest(resource="OWNER"))

    assert result.canonical_resource == "user"
    assert result.aliases == ["account", "owner", "user"]
    assert [(item.value, item.value_type) for item in result.identifiers] == [
        (42, "integer"),
        ("42", "string"),
    ]
    assert result.recommended_id == 42
    assert result.operations[0].operation_key == "POST /users"
    assert result.operations[0].access_mode == "write"
    assert result.operations[0].latest_seen_at == later_seen
    assert result.operations[0].id_field_aliases == ["id"]
    assert result.operations[0].selectors == ["$.id"]


def test_list_resources_loads_aliases_in_one_bounded_batch_query(
    tmp_path: Path,
) -> None:
    from sqlalchemy import event

    from restscope.agent.api_behavior_monitor import (
        DetectedResourceGroup,
        MonitoredOperation,
    )

    catalog, engine = _catalog_with_engine(tmp_path)
    for resource_name in ("alpha", "beta", "gamma"):
        catalog.record_groups(
            operation=MonitoredOperation(
                operation_key=f"GET /{resource_name}s",
                method="GET",
                path=f"/{resource_name}s",
            ),
            groups=[
                DetectedResourceGroup(
                    group_path="$",
                    resource_name=resource_name,
                    resource_aliases=[f"{resource_name}-extra"],
                    id_field_name="id",
                    id_selector="$.id",
                    identifier_values=[1],
                    classification_source="exact_id",
                )
            ],
        )

    select_statements: list[str] = []

    def capture_select(_connection, _cursor, statement, _parameters, _context, _many):
        if statement.lstrip().upper().startswith("SELECT"):
            select_statements.append(statement)

    event.listen(engine, "before_cursor_execute", capture_select)
    try:
        resources = catalog.list_resources(limit=3, aliases_per_resource=1)
    finally:
        event.remove(engine, "before_cursor_execute", capture_select)

    assert [resource.canonical_name for resource in resources] == [
        "alpha",
        "beta",
        "gamma",
    ]
    assert [resource.aliases for resource in resources] == [
        ["alpha"],
        ["beta"],
        ["gamma"],
    ]
    assert len(select_statements) == 2


def test_catalog_returns_delete_identifiers_and_filters_by_typed_value(
    tmp_path: Path,
) -> None:
    from restscope.agent.api_behavior_monitor import (
        DetectedResourceGroup,
        MonitoredOperation,
        ResourceLookupRequest,
    )

    catalog = _catalog(tmp_path)
    observed_at = datetime(2026, 7, 23, 11, tzinfo=timezone.utc)
    catalog.record_groups(
        operation=MonitoredOperation(
            operation_key="DELETE /commits/{commitId}",
            method="DELETE",
            path="/commits/{commitId}",
        ),
        groups=[
            DetectedResourceGroup(
                group_path="$",
                resource_name="commit",
                resource_aliases=["commit"],
                id_field_name="sha",
                id_selector="$.sha",
                identifier_values=["abc123"],
                classification_source="llm",
            )
        ],
        observed_at=observed_at,
    )

    result = catalog.lookup(
        ResourceLookupRequest(resource="commit", id_value="abc123")
    )

    assert result.recommended_id == "abc123"
    assert [item.value for item in result.identifiers] == ["abc123"]
    assert result.operations[0].access_mode == "write"


def test_catalog_latest_error_is_cleared_by_later_group_success(tmp_path: Path) -> None:
    from restscope.agent.api_behavior_monitor import (
        DetectedResourceGroup,
        MonitoredOperation,
        ResourceLookupRequest,
        ResourceMonitorWarning,
    )

    catalog = _catalog(tmp_path)
    operation = MonitoredOperation(
        operation_key="GET /projects/{projectId}",
        method="GET",
        path="/projects/{projectId}",
    )
    group = DetectedResourceGroup(
        group_path="$",
        resource_name="project",
        resource_aliases=["project"],
        id_field_name="id",
        id_selector="$.id",
        identifier_values=[7],
        classification_source="exact_id",
    )
    catalog.record_groups(operation=operation, groups=[group])
    catalog.record_error(
        operation=operation,
        group_path="$",
        warning=ResourceMonitorWarning(
            code="expected_resource_id_missing",
            message="Expected resource identifier is missing",
            issues=["$.id"],
        ),
    )
    assert [
        error.code
        for error in catalog.lookup(
            ResourceLookupRequest(resource="project")
        ).errors
    ] == ["expected_resource_id_missing"]

    catalog.record_groups(
        operation=operation,
        groups=[group],
    )
    assert catalog.lookup(
        ResourceLookupRequest(resource="project")
    ).errors == []


def test_catalog_rolls_back_whole_response_when_one_group_conflicts(
    tmp_path: Path,
) -> None:
    import pytest

    from restscope.agent.api_behavior_monitor import (
        DetectedResourceGroup,
        MonitoredOperation,
        ResourceLookupRequest,
    )
    from restscope.db.repositories import ResourceCatalogConflict

    catalog = _catalog(tmp_path)
    seed_operation = MonitoredOperation(
        operation_key="POST /seed",
        method="POST",
        path="/seed",
    )
    for group_path, resource_name in (("$.user", "user"), ("$.project", "project")):
        catalog.record_groups(
            operation=seed_operation.model_copy(
                update={"operation_key": f"POST /{resource_name}s"}
            ),
            groups=[
                DetectedResourceGroup(
                    group_path=group_path,
                    resource_name=resource_name,
                    resource_aliases=[resource_name],
                    id_field_name="id",
                    id_selector=f"{group_path}.id",
                    identifier_values=[1],
                    classification_source="exact_id",
                )
            ],
        )

    with pytest.raises(ResourceCatalogConflict):
        catalog.record_groups(
            operation=MonitoredOperation(
                operation_key="GET /dashboard",
                method="GET",
                path="/dashboard",
            ),
            groups=[
                DetectedResourceGroup(
                    group_path="$.team",
                    resource_name="team",
                    resource_aliases=["team"],
                    id_field_name="id",
                    id_selector="$.team.id",
                    identifier_values=[7],
                    classification_source="exact_id",
                ),
                DetectedResourceGroup(
                    group_path="$.owner",
                    resource_name="user",
                    resource_aliases=["user", "project"],
                    id_field_name="id",
                    id_selector="$.owner.id",
                    identifier_values=[9],
                    classification_source="exact_id",
                ),
            ],
        )

    assert catalog.lookup(ResourceLookupRequest(resource="team")).status == "not_found"
    assert catalog.list_rules("GET /dashboard") == []


def test_lookup_preserves_operation_specific_aliases_and_all_resource_usage(
    tmp_path: Path,
) -> None:
    from restscope.agent.api_behavior_monitor import (
        DetectedResourceGroup,
        MonitoredOperation,
        ResourceLookupRequest,
    )

    catalog = _catalog(tmp_path)
    catalog.record_groups(
        operation=MonitoredOperation(
            operation_key="POST /users",
            method="POST",
            path="/users",
        ),
        groups=[
            DetectedResourceGroup(
                group_path="$",
                resource_name="user",
                resource_aliases=["user"],
                id_field_name="id",
                id_selector="$.id",
                identifier_values=[1],
                classification_source="exact_id",
            )
        ],
    )
    catalog.record_groups(
        operation=MonitoredOperation(
            operation_key="GET /owners/{ownerId}",
            method="GET",
            path="/owners/{ownerId}",
        ),
        groups=[
            DetectedResourceGroup(
                group_path="$",
                resource_name="user",
                resource_aliases=["owner"],
                id_field_name="ownerId",
                id_selector="$.ownerId",
                identifier_values=[2],
                classification_source="llm",
            )
        ],
    )

    resource_result = catalog.lookup(
        ResourceLookupRequest(resource="user", limit=1)
    )
    id_result = catalog.lookup(
        ResourceLookupRequest(resource="user", id_value=1)
    )

    assert resource_result.total == 2
    assert resource_result.truncated is True
    assert {
        item.operation_key: item.resource_aliases
        for item in resource_result.operations
    } == {
        "POST /users": ["user"],
        "GET /owners/{ownerId}": ["owner"],
    }
    assert [item.operation_key for item in id_result.operations] == [
        "POST /users"
    ]


def test_same_response_identifiers_have_stable_recommendation_order(
    tmp_path: Path,
) -> None:
    from restscope.agent.api_behavior_monitor import (
        DetectedResourceGroup,
        MonitoredOperation,
        ResourceLookupRequest,
    )

    catalog = _catalog(tmp_path)
    catalog.record_groups(
        operation=MonitoredOperation(
            operation_key="GET /users",
            method="GET",
            path="/users",
        ),
        groups=[
            DetectedResourceGroup(
                group_path="$[]",
                resource_name="user",
                resource_aliases=["user"],
                id_field_name="id",
                id_selector="$[].id",
                identifier_values=[2, 1],
                classification_source="exact_id",
            )
        ],
    )

    result = catalog.lookup(ResourceLookupRequest(resource="user"))

    assert [item.value for item in result.identifiers] == [1, 2]
    assert result.recommended_id == 1
