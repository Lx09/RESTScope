"""Behavior scenarios for top-level Resource Identifier discovery."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace


class StubLLMClient:
    """Simulate Harness validation and unlimited correction at the System Agent seam."""

    def __init__(self, *parsed_responses: dict[str, object]) -> None:
        from restscope.observability import TracingRuntime

        self.parsed_responses = list(parsed_responses)
        self.requests: list[SimpleNamespace] = []
        self.tracing_runtime = TracingRuntime.disabled()

    def run_system_agent(self, profile_name: str, task):
        """Keep requesting output until one result satisfies the bounded task contract."""
        from restscope.agent import AgentError, AgentUsage, SystemAgentResult
        from restscope.api_behavior_monitor.resource_identifiers.prompts import (
            IdentifierSelectionDecision,
            validate_identifier_system_output,
        )
        from restscope.llm import LLMMessage

        messages = [
            LLMMessage(role="system", content="Harness contract"),
            LLMMessage(role="user", content=task.objective),
        ]
        while self.parsed_responses:
            output = self.parsed_responses.pop(0)
            request = SimpleNamespace(
                metadata={"role": profile_name},
                messages=list(messages),
                response_format="json_schema",
                json_schema={"allowed_paths": list(task.allowed_result_paths)},
            )
            self.requests.append(request)
            try:
                decision = IdentifierSelectionDecision.model_validate(output)
                errors = validate_identifier_system_output(decision, task)
            except Exception as exc:
                errors = (str(exc),)
            if not errors:
                return SystemAgentResult(
                    session_id="system-test",
                    profile_name=profile_name,
                    status="completed",
                    output=output,
                    usage=AgentUsage(model_outputs=len(self.requests)),
                )
            messages.extend(
                (
                    LLMMessage(role="assistant", content=str(output)),
                    LLMMessage(
                        role="user",
                        content="CORRECTION: " + errors[0],
                    ),
                )
            )
        return SystemAgentResult(
            session_id="system-test",
            profile_name=profile_name,
            status="failed",
            error=AgentError(
                code="provider_invoke_failed",
                message="Scripted provider responses were exhausted.",
            ),
        )


def _selection(
    *field_aliases: str,
    path: str | None = None,
) -> dict[str, object]:
    """Build one ordered identifier result, or null when no aliases are supplied."""
    return {
        "identifier": (
            {"path": path, "fields": list(field_aliases)}
            if field_aliases
            else None
        )
    }


def _catalog(tmp_path: Path):
    """Create one isolated real Catalog Adapter for tracker scenarios."""
    from restscope.api_behavior_monitor.resource_identifiers.catalog import ResourceCatalog
    from restscope.db import Base, SqlAlchemyResourceCatalogUnitOfWork, create_engine_from_url, make_session_factory

    engine = create_engine_from_url(f"sqlite:///{tmp_path / 'monitor.sqlite'}")
    Base.metadata.create_all(engine)
    session_factory = make_session_factory(engine)
    return ResourceCatalog(lambda: SqlAlchemyResourceCatalogUnitOfWork(session_factory))


def _tracker(tmp_path: Path, client: StubLLMClient):
    """Bind the public Tracker Interface to real persistence and a fake System Agent."""
    from restscope.api_behavior_monitor.resource_identifiers.tracker import ResourceIdentifierTracker

    catalog = _catalog(tmp_path)
    return ResourceIdentifierTracker(catalog=catalog, system_agent_runner=client), catalog


def _observation(
    *,
    operation_key: str = "POST /users",
    method: str = "POST",
    path: str = "/users",
    body: object,
):
    """Build one bounded successful JSON observation."""
    from restscope.api_behavior_monitor.resource_identifiers.schemas import MonitoredOperation, ResourceObservation

    return ResourceObservation(
        operation=MonitoredOperation(operation_key=operation_key, method=method, path=path),
        status_code=201,
        media_type="application/json",
        body=body,
    )


def test_exact_id_still_requires_the_system_agent(tmp_path: Path) -> None:
    """A familiar field name is evidence, not a deterministic decision."""
    from restscope.api_behavior_monitor import ResourceLookupRequest

    client = StubLLMClient(_selection("I1"))
    tracker, catalog = _tracker(tmp_path, client)

    result = tracker.observe(_observation(body={"id": 42, "name": "Ada"}))

    assert result.identifiers_recorded == 1
    assert len(client.requests) == 1
    record = catalog.lookup(ResourceLookupRequest(resource="user")).identifiers[0]
    assert [(item.name, item.value) for item in record.components] == [("id", 42)]


def test_prompt_contains_all_fields_and_full_path_evidence(tmp_path: Path) -> None:
    """The Agent receives one complete decision context without response values."""
    path = "/users/{organizationId}/{userId}"
    client = StubLLMClient(_selection("I1", "I2", path=path))
    tracker, _catalog = _tracker(tmp_path, client)
    observation = _observation(body={"org_id": "o1", "user_id": 7, "profile": {"id": 9}}).model_copy(
        update={"related_identifier_paths": ("/users/{userId}", path)}
    )

    tracker.observe(observation)

    prompt = client.requests[0].messages[1].content
    assert 'field: "org_id"' in prompt
    assert 'field: "user_id"' in prompt
    assert 'field: "id"' not in prompt
    assert 'path: "/users/{userId}"' in prompt
    assert f'path: "{path}"' in prompt
    assert "o1" not in prompt


def test_more_than_one_hundred_candidates_fails_closed(tmp_path: Path) -> None:
    """An oversized decision is skipped instead of truncated or split."""
    client = StubLLMClient()
    tracker, _catalog = _tracker(tmp_path, client)

    result = tracker.observe(
        _observation(body={f"field{index}": str(index) for index in range(101)})
    )

    assert result.status == "warning"
    assert result.warning is not None
    assert result.warning.code == "resource_monitor_evidence_limit_exceeded"
    assert client.requests == []


def test_more_than_one_hundred_paths_fails_closed(tmp_path: Path) -> None:
    """Excess OpenAPI path evidence becomes a warning before any Agent call."""
    client = StubLLMClient()
    tracker, _catalog = _tracker(tmp_path, client)
    observation = _observation(body={"id": 1}).model_copy(
        update={
            "related_identifier_paths": tuple(
                f"/users/{{tenant{index}}}" for index in range(101)
            )
        }
    )

    result = tracker.observe(observation)

    assert result.status == "warning"
    assert result.warning is not None
    assert result.warning.code == "resource_monitor_evidence_limit_exceeded"
    assert client.requests == []


def test_schema_only_field_is_not_a_candidate(tmp_path: Path) -> None:
    """OpenAPI may enrich an observed field but cannot invent an absent one."""
    client = StubLLMClient(_selection("I1"))
    tracker, _catalog = _tracker(tmp_path, client)
    observation = _observation(body={"name": "Ada"}).model_copy(
        update={
            "response_schema_fields": [
                {"selector": "$.id", "name": "id", "type": "integer"},
                {"selector": "$.name", "name": "name", "type": "string", "format": "slug"},
            ]
        }
    )

    tracker.observe(observation)

    prompt = client.requests[0].messages[1].content
    assert 'field: "name"' in prompt
    assert 'field: "id"' not in prompt
    assert 'format: "slug"' in prompt


def test_null_boolean_and_float_values_do_not_hide_an_observed_integer(
    tmp_path: Path,
) -> None:
    """Invalid scalar kinds are ignored per item instead of tainting a field."""
    from restscope.api_behavior_monitor import ResourceLookupRequest

    client = StubLLMClient(_selection("I1"))
    tracker, catalog = _tracker(tmp_path, client)

    result = tracker.observe(
        _observation(
            operation_key="GET /users",
            method="GET",
            path="/users",
            body=[
                {"id": None, "active": True, "score": 1.5},
                {"id": 7, "active": False, "score": 2.5},
            ],
        )
    )

    prompt = client.requests[0].messages[1].content
    assert 'field: "id"' in prompt
    assert 'field: "active"' not in prompt
    assert 'field: "score"' not in prompt
    assert result.identifiers_recorded == 1
    records = catalog.lookup(ResourceLookupRequest(resource="user")).identifiers
    assert records[0].components[0].value == 7


def test_learned_composite_rule_skips_incomplete_rows(tmp_path: Path) -> None:
    """A later incomplete item produces a warning and never a partial tuple."""
    from restscope.api_behavior_monitor import ResourceLookupRequest

    path = "/memberships/{organizationId}/{userId}"
    client = StubLLMClient(_selection("I1", "I2", path=path))
    tracker, catalog = _tracker(tmp_path, client)
    first = _observation(
        operation_key="GET /memberships",
        method="GET",
        path="/memberships",
        body=[{"organization_id": "o1", "user_id": 7}],
    ).model_copy(update={"related_identifier_paths": (path,)})
    tracker.observe(first)

    result = tracker.observe(first.model_copy(update={"body": [{"organization_id": "o2"}]}))

    assert result.status == "warning"
    assert len(client.requests) == 1
    records = catalog.lookup(ResourceLookupRequest(resource="membership")).identifiers
    assert len(records) == 1


def test_harness_correction_has_no_retry_limit(tmp_path: Path) -> None:
    """At least three invalid final outputs may precede a valid decision."""
    client = StubLLMClient(
        {"identifier": {"path": None, "fields": ["forged"]}},
        {"identifier": {"path": None, "fields": ["I1", "I2"]}},
        {"extra": "field"},
        _selection("I1"),
    )
    tracker, _catalog = _tracker(tmp_path, client)

    assert tracker.observe(_observation(body={"id": 1, "name": "Ada"})).status == "updated"
    assert len(client.requests) == 4
    assert all("CORRECTION:" in request.messages[-1].content for request in client.requests[1:])
