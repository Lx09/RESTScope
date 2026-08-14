"""Resource Monitor derivation into definitions, roles, and current state."""

from __future__ import annotations

from datetime import UTC, datetime


def _catalog():
    """Create a real in-memory Catalog for one Resource Monitor scenario."""

    from restscope.api_behavior_monitor.catalog import APIBehaviorCatalog
    from restscope.db import (
        Base,
        SqlAlchemyAPIBehaviorUnitOfWork,
        create_engine_from_url,
        make_session_factory,
    )

    engine = create_engine_from_url("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    sessions = make_session_factory(engine)
    return APIBehaviorCatalog(
        lambda: SqlAlchemyAPIBehaviorUnitOfWork(sessions)
    )


class _SelectIdAgent:
    """Select ``id`` and assign operation states without reading instance data."""

    identity_calls = 0
    state_calls = 0

    def run_system_agent(self, profile_name, task):
        """Return one Harness-shaped validated identifier decision."""

        from restscope.agent import SystemAgentResult

        if profile_name == "resource-identifier-selector":
            self.identity_calls += 1
            assert task.allowed_result_aliases[0] == "I1"
            output = {"identifier": {"path": None, "fields": ["I1"]}}
        else:
            self.state_calls += 1
            assert profile_name == "resource-state-selector"
            assert "Ada" not in task.objective
            assert "Shanghai" not in task.objective
            output = {
                "existing_state": None,
                "new_state": "deleted" if "DELETE" in task.objective else "active",
            }
        return SystemAgentResult(
            session_id=f"resource-{self.identity_calls + self.state_calls}",
            profile_name=profile_name,
            status="completed",
            output=output,
        )


def _observation(catalog, operation, index: int) -> str:
    """Persist the concrete successful Test Case required by a resource update."""

    from restscope.api_behavior_monitor.catalog import ObservationWrite

    return catalog.record_observation(
        ObservationWrite(
            operation_id=operation.operation_id,
            timestamp=datetime(2026, 8, 14, index, tzinfo=UTC),
            outcome_kind="http",
            request_json={"path": operation.path},
            status_code=200,
            response_headers={"content-type": "application/json"},
            response_body=b"{}",
            body_format="json",
        )
    ).observation_id


def test_unknown_resource_uses_agent_once_then_reuses_identity_and_logically_deletes() -> None:
    """Known identity fields avoid repeated model calls and DELETE hides state."""

    from restscope.api_behavior_monitor.catalog import OperationDefinition
    from restscope.api_behavior_monitor.resource_monitor import ResourceResponseTracker

    catalog = _catalog()
    get_operation = OperationDefinition(
        operation_id="GET /users",
        method="GET",
        path="/users",
    )
    delete_operation = OperationDefinition(
        operation_id="DELETE /users/{id}",
        method="DELETE",
        path="/users/{id}",
    )
    catalog.ensure_operation(get_operation)
    catalog.ensure_operation(delete_operation)
    agent = _SelectIdAgent()
    tracker = ResourceResponseTracker(
        catalog=catalog,
        system_agent_runner=agent,
    )

    first = tracker.observe(
        operation=get_operation,
        observation_id=_observation(catalog, get_operation, 0),
        body={"items": [{"id": 7, "name": "Ada", "profile": {"city": "Xi'an"}}]},
    )
    second = tracker.observe(
        operation=get_operation,
        observation_id=_observation(catalog, get_operation, 1),
        body={"items": [{"id": 7, "name": None, "profile": {"city": "Shanghai"}}]},
    )
    deleted = tracker.observe(
        operation=delete_operation,
        observation_id=_observation(catalog, delete_operation, 2),
        body={"id": 7},
    )

    assert agent.identity_calls == 1
    assert agent.state_calls == 2
    assert first.resources[0].name == "users"
    assert first.resources[0].identity_fields == ("id",)
    assert second.conflicts == ()
    assert deleted.instances[0].current_state_json["_deleted"] is True
    active, active_total = catalog.list_resource_instances(
        resource_type="users",
        offset=0,
        limit=10,
    )
    all_instances, all_total = catalog.list_resource_instances(
        resource_type="users",
        offset=0,
        limit=10,
        include_deleted=True,
    )
    assert active == []
    assert active_total == 0
    assert all_total == 1
    assert all_instances[0].current_state_json == {
        "id": 7,
        "name": "Ada",
        "profile": {"city": "Shanghai"},
        "_deleted": True,
    }
    assert all_instances[0].semantic_state == "deleted"
    progress = catalog.read_test_progress()
    assert [
        (item.resource_type, item.semantic_state, item.instance_count)
        for item in progress.resource_states
    ] == [("users", "deleted", 1)]


def test_state_agent_failure_writes_nothing_and_retries_on_next_observation() -> None:
    """A failed missing-edge decision leaves no partial authority or instance."""

    import pytest

    from restscope.agent import SystemAgentResult
    from restscope.api_behavior_monitor.catalog import OperationDefinition
    from restscope.api_behavior_monitor.resource_monitor import ResourceResponseTracker

    class FailsFirstState:
        """Identify the resource each time but fail the first state decision."""

        state_calls = 0

        def run_system_agent(self, profile_name, task):
            """Return one terminal state failure followed by a valid retry."""

            if profile_name == "resource-identifier-selector":
                return SystemAgentResult(
                    session_id="identity",
                    profile_name=profile_name,
                    status="completed",
                    output={"identifier": {"path": None, "fields": ["I1"]}},
                )
            self.state_calls += 1
            if self.state_calls == 1:
                return SystemAgentResult(
                    session_id="state-failed",
                    profile_name=profile_name,
                    status="failed",
                    error={"code": "provider_failed", "message": "provider failed"},
                )
            return SystemAgentResult(
                session_id="state-complete",
                profile_name=profile_name,
                status="completed",
                output={"existing_state": None, "new_state": "active"},
            )

    catalog = _catalog()
    operation = OperationDefinition(
        operation_id="GET /users",
        method="GET",
        path="/users",
    )
    catalog.ensure_operation(operation)
    agent = FailsFirstState()
    tracker = ResourceResponseTracker(catalog=catalog, system_agent_runner=agent)

    with pytest.raises(RuntimeError, match="Resource State"):
        tracker.observe(
            operation=operation,
            observation_id=_observation(catalog, operation, 0),
            body={"items": [{"id": 7, "secret": "never prompt this"}]},
        )
    assert catalog.list_resources(offset=0, limit=10) == ([], 0)

    result = tracker.observe(
        operation=operation,
        observation_id=_observation(catalog, operation, 1),
        body={"items": [{"id": 7}]},
    )

    assert agent.state_calls == 2
    assert result.instances[0].semantic_state == "active"


def test_new_operation_reuses_an_existing_resource_state_alias() -> None:
    """An equivalent meaning creates a new edge without inventing a state name."""

    from restscope.agent import SystemAgentResult
    from restscope.api_behavior_monitor.catalog import OperationDefinition
    from restscope.api_behavior_monitor.resource_monitor import ResourceResponseTracker

    class ReusesActive:
        """Create ``active`` once, then select that exact established alias."""

        def __init__(self) -> None:
            """Start with no state decisions observed."""

            self.state_tasks = []

        def run_system_agent(self, profile_name, task):
            """Return valid identity and state decisions for two operations."""

            if profile_name == "resource-identifier-selector":
                output = {"identifier": {"path": None, "fields": ["I1"]}}
            else:
                self.state_tasks.append(task)
                output = (
                    {"existing_state": None, "new_state": "active"}
                    if not task.allowed_result_aliases
                    else {"existing_state": "active", "new_state": None}
                )
            return SystemAgentResult(
                session_id=f"reuse-{len(self.state_tasks)}",
                profile_name=profile_name,
                status="completed",
                output=output,
            )

    catalog = _catalog()
    get_operation = OperationDefinition(
        operation_id="GET /users",
        method="GET",
        path="/users",
    )
    patch_operation = OperationDefinition(
        operation_id="PATCH /users/{id}",
        method="PATCH",
        path="/users/{id}",
    )
    catalog.ensure_operation(get_operation)
    catalog.ensure_operation(patch_operation)
    agent = ReusesActive()
    tracker = ResourceResponseTracker(catalog=catalog, system_agent_runner=agent)

    tracker.observe(
        operation=get_operation,
        observation_id=_observation(catalog, get_operation, 0),
        body={"items": [{"id": 7}]},
    )
    tracker.observe(
        operation=patch_operation,
        observation_id=_observation(catalog, patch_operation, 1),
        body={"id": 7},
    )
    tracker.observe(
        operation=patch_operation,
        observation_id=_observation(catalog, patch_operation, 2),
        body={"id": 7},
    )

    assert len(agent.state_tasks) == 2
    assert agent.state_tasks[-1].allowed_result_aliases == ("active",)
    events, total = catalog.list_resource_state_events(
        resource_type="users",
        offset=0,
        limit=10,
    )
    assert total == 1
    assert events[0].current_state == "active"


def test_json_without_an_identifiable_instance_creates_no_state() -> None:
    """A complete 2xx JSON object still needs a stable scalar identity."""

    from restscope.api_behavior_monitor.catalog import OperationDefinition
    from restscope.api_behavior_monitor.resource_monitor import ResourceResponseTracker

    catalog = _catalog()
    operation = OperationDefinition(
        operation_id="GET /users",
        method="GET",
        path="/users",
    )
    catalog.ensure_operation(operation)
    agent = _SelectIdAgent()
    tracker = ResourceResponseTracker(catalog=catalog, system_agent_runner=agent)

    result = tracker.observe(
        operation=operation,
        observation_id=_observation(catalog, operation, 0),
        body={"items": [{"profile": {"labels": []}}]},
    )

    assert result.resources == ()
    assert agent.identity_calls == 0
    assert agent.state_calls == 0
    assert catalog.list_resources(offset=0, limit=10) == ([], 0)
