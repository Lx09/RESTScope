"""Resource Monitor derivation into definitions, roles, and current state."""

from __future__ import annotations


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
    """Select the alphabetically first candidate, which is ``id`` here."""

    calls = 0

    def run_system_agent(self, profile_name, task):
        """Return one Harness-shaped validated identifier decision."""

        from restscope.agent import SystemAgentResult

        self.calls += 1
        assert profile_name == "resource-identifier-selector"
        assert task.allowed_result_aliases[0] == "I1"
        return SystemAgentResult(
            session_id=f"resource-{self.calls}",
            profile_name=profile_name,
            status="completed",
            output={"identifier": {"path": None, "fields": ["I1"]}},
        )


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
        body={"items": [{"id": 7, "name": "Ada", "profile": {"city": "Xi'an"}}]},
    )
    second = tracker.observe(
        operation=get_operation,
        body={"items": [{"id": 7, "name": None, "profile": {"city": "Shanghai"}}]},
    )
    deleted = tracker.observe(
        operation=delete_operation,
        body={"id": 7},
    )

    assert agent.calls == 1
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
