from __future__ import annotations

from pathlib import Path


class StubLLMClient:
    def __init__(self, *parsed_responses: dict) -> None:
        self.parsed_responses = list(parsed_responses)
        self.requests = []

    def invoke(self, request):
        from restscope.llm import LLMResponse

        self.requests.append(request)
        if not self.parsed_responses:
            raise AssertionError("Resource Monitor made an unexpected LLM call")
        return LLMResponse(
            provider=request.provider,
            model=request.model,
            parsed_json=self.parsed_responses.pop(0),
        )


def _classification(
    *,
    group_id: str = "g1",
    represents_resource: bool,
    canonical_resource_name: str | None = None,
    identifier_candidate_id: str | None = None,
) -> dict:
    """Build the deliberately minimal Resource Monitor LLM result."""

    return {
        "groups": [
            {
                "group_id": group_id,
                "represents_resource": represents_resource,
                **(
                    {"canonical_resource_name": canonical_resource_name}
                    if canonical_resource_name is not None
                    else {}
                ),
                **(
                    {"identifier_candidate_id": identifier_candidate_id}
                    if identifier_candidate_id is not None
                    else {}
                ),
            }
        ]
    }


def _catalog(tmp_path: Path):
    from restscope.agent.resource_monitor import ResourceCatalog
    from restscope.db import (
        Base,
        SqlAlchemyResourceCatalogUnitOfWork,
        create_engine_from_url,
        make_session_factory,
    )

    engine = create_engine_from_url(f"sqlite:///{tmp_path / 'monitor.sqlite'}")
    Base.metadata.create_all(engine)
    session_factory = make_session_factory(engine)
    return ResourceCatalog(
        lambda: SqlAlchemyResourceCatalogUnitOfWork(session_factory)
    )


def _agent(tmp_path: Path, client: StubLLMClient):
    from restscope.agent.resource_monitor import ResourceMonitorAgent
    from restscope.llm import LLMModelConfig

    catalog = _catalog(tmp_path)
    agent = ResourceMonitorAgent(
        catalog=catalog,
        client=client,
        model=LLMModelConfig(
            role="resource_monitor",
            provider="stub",
            model="fast-stub",
        ),
    )
    return agent, catalog


def _observation(
    *,
    operation_key: str = "POST /users",
    method: str = "POST",
    path: str = "/users",
    body,
):
    from restscope.agent.resource_monitor import MonitoredOperation, ResourceObservation

    return ResourceObservation(
        operation=MonitoredOperation(
            operation_key=operation_key,
            method=method,
            path=path,
        ),
        status_code=201,
        media_type="application/json",
        body=body,
    )


def test_model_prompt_is_minimal_and_never_exposes_identifier_values(
    tmp_path: Path,
) -> None:
    client = StubLLMClient(
        _classification(
            represents_resource=True,
            canonical_resource_name="commit",
            identifier_candidate_id="c1",
        )
    )
    agent, _catalog = _agent(tmp_path, client)

    result = agent.observe(
        _observation(
            operation_key="POST /commits",
            path="/commits",
            body={"sha": "secret-abc123", "message": "initial"},
        )
    )

    assert result.status == "updated"
    payload = __import__("json").loads(client.requests[0].messages[1].content)
    assert set(payload) == {"operation", "known_resource_names", "groups"}
    assert payload["operation"] == {"method": "POST", "path": "/commits"}
    assert payload["known_resource_names"] == []
    group = payload["groups"][0]
    assert set(group) == {
        "group_id",
        "response_location",
        "resource_name_hint",
        "identifier_candidates",
    }
    assert group["group_id"] == "g1"
    assert group["response_location"] == "$"
    assert group["resource_name_hint"] == "commit"
    candidate = group["identifier_candidates"][0]
    assert set(candidate) == {
        "candidate_id",
        "field_path",
        "value_types",
        "observed_in_response",
    }
    assert candidate["candidate_id"] == "c1"
    assert candidate["field_path"] == "sha"
    assert candidate["value_types"] == ["string"]
    assert candidate["observed_in_response"] is True
    prompt = "\n".join(message.content for message in client.requests[0].messages)
    for forbidden in (
        "secret-abc123",
        "operation_key",
        "selector",
        "required",
        "observed_count",
        "resource_id",
        "aliases",
        "truncated",
        "total",
    ):
        assert forbidden not in prompt


def test_exact_id_is_recorded_without_calling_llm(tmp_path: Path) -> None:
    from restscope.agent.resource_monitor import ResourceLookupRequest

    client = StubLLMClient()
    agent, catalog = _agent(tmp_path, client)

    result = agent.observe(_observation(body={"id": 42, "name": "Ada"}))

    assert result.status == "updated"
    assert result.identifiers_recorded == 1
    assert client.requests == []
    lookup = catalog.lookup(ResourceLookupRequest(resource="user"))
    assert lookup.recommended_id == 42
    assert lookup.operations[0].id_field_aliases == ["id"]


def test_semantic_identifier_uses_one_fast_model_call_without_exposing_value(
    tmp_path: Path,
) -> None:
    from restscope.agent.resource_monitor import ResourceLookupRequest

    client = StubLLMClient(
        _classification(
            represents_resource=True,
            canonical_resource_name="commit",
            identifier_candidate_id="c1",
        )
    )
    agent, catalog = _agent(tmp_path, client)

    result = agent.observe(
        _observation(
            operation_key="POST /commits",
            path="/commits",
            body={"sha": "secret-abc123", "message": "initial"},
        )
    )

    assert result.status == "updated"
    assert len(client.requests) == 1
    assert client.requests[0].metadata["role"] == "resource_monitor"
    prompt = "\n".join(message.content for message in client.requests[0].messages)
    assert "secret-abc123" not in prompt
    assert catalog.lookup(
        ResourceLookupRequest(resource="commit")
    ).recommended_id == "secret-abc123"


def test_unresolved_top_level_groups_are_batched_into_one_llm_call(
    tmp_path: Path,
) -> None:
    from restscope.agent.resource_monitor import ResourceLookupRequest

    client = StubLLMClient(
        {
            "groups": [
                {
                    "group_id": "g1",
                    "represents_resource": True,
                    "canonical_resource_name": "user",
                    "identifier_candidate_id": "c1",
                },
                {
                    "group_id": "g2",
                    "represents_resource": True,
                    "canonical_resource_name": "project",
                    "identifier_candidate_id": "c1",
                },
            ]
        }
    )
    agent, catalog = _agent(tmp_path, client)

    result = agent.observe(
        _observation(
            operation_key="GET /dashboard",
            method="GET",
            path="/dashboard",
            body={
                "user": {"userId": 7, "name": "Ada"},
                "project": {"projectKey": "restscope"},
            },
        )
    )

    assert result.groups_processed == 2
    assert len(client.requests) == 1
    assert catalog.lookup(ResourceLookupRequest(resource="user")).recommended_id == 7
    assert (
        catalog.lookup(ResourceLookupRequest(resource="project")).recommended_id
        == "restscope"
    )


def test_learned_rule_is_reused_and_missing_identifier_returns_warning(
    tmp_path: Path,
) -> None:
    from restscope.agent.resource_monitor import ResourceLookupRequest

    client = StubLLMClient(
        _classification(
            represents_resource=True,
            canonical_resource_name="commit",
            identifier_candidate_id="c1",
        )
    )
    agent, catalog = _agent(tmp_path, client)
    first = _observation(
        operation_key="GET /commits/{commitId}",
        method="GET",
        path="/commits/{commitId}",
        body={"sha": "first"},
    )

    assert agent.observe(first).status == "updated"
    assert agent.observe(first.model_copy(update={"body": {"sha": "second"}})).status == "updated"
    missing = agent.observe(first.model_copy(update={"body": {"message": "missing"}}))

    assert len(client.requests) == 1
    assert missing.status == "warning"
    assert missing.warning is not None
    assert missing.warning.code == "expected_resource_id_missing"
    lookup = catalog.lookup(ResourceLookupRequest(resource="commit"))
    assert [item.value for item in lookup.identifiers] == ["second", "first"]
    assert [error.code for error in lookup.errors] == [
        "expected_resource_id_missing"
    ]


def test_exact_id_can_use_fast_model_to_merge_new_resource_alias(
    tmp_path: Path,
) -> None:
    from restscope.agent.resource_monitor import (
        DetectedResourceGroup,
        MonitoredOperation,
        ResourceLookupRequest,
    )

    client = StubLLMClient(
        _classification(
            represents_resource=True,
            canonical_resource_name="user",
            identifier_candidate_id="c1",
        )
    )
    agent, catalog = _agent(tmp_path, client)
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

    result = agent.observe(
        _observation(
            operation_key="GET /documents/{documentId}",
            method="GET",
            path="/documents/{documentId}",
            body={"owner": {"id": 2, "name": "Grace"}},
        )
    )

    assert result.status == "updated"
    assert len(client.requests) == 1
    payload = __import__("json").loads(client.requests[0].messages[1].content)
    assert payload["known_resource_names"] == ["user"]
    assert "matched_canonical_name" not in payload["groups"][0]
    assert payload["groups"][0]["locked_identifier_candidate_id"] == "c1"
    lookup = catalog.lookup(ResourceLookupRequest(resource="owner"))
    assert lookup.canonical_resource == "user"
    assert {item.value for item in lookup.identifiers} == {1, 2}


def test_known_resource_alias_is_exposed_as_locked_canonical_name(
    tmp_path: Path,
) -> None:
    from restscope.agent.resource_monitor import (
        DetectedResourceGroup,
        MonitoredOperation,
    )

    client = StubLLMClient(
        _classification(
            represents_resource=True,
            canonical_resource_name="user",
            identifier_candidate_id="c1",
        )
    )
    agent, catalog = _agent(tmp_path, client)
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
                resource_aliases=["user", "owner"],
                id_field_name="id",
                id_selector="$.id",
                identifier_values=[1],
                classification_source="exact_id",
            )
        ],
    )

    result = agent.observe(
        _observation(
            operation_key="GET /documents/{documentId}",
            method="GET",
            path="/documents/{documentId}",
            body={"owner": {"userKey": 2}},
        )
    )

    assert result.status == "updated"
    payload = __import__("json").loads(client.requests[0].messages[1].content)
    assert payload["groups"][0]["matched_canonical_name"] == "user"


def test_no_resource_decision_is_cached_for_later_success_responses(
    tmp_path: Path,
) -> None:
    client = StubLLMClient(
        _classification(represents_resource=False)
    )
    agent, _catalog = _agent(tmp_path, client)
    observation = _observation(
        operation_key="GET /health",
        method="GET",
        path="/health",
        body={"status": "ok", "uptime": 100},
    )

    assert agent.observe(observation).status == "ignored"
    assert agent.observe(
        observation.model_copy(update={"body": {"status": "ok", "uptime": 200}})
    ).status == "ignored"
    assert len(client.requests) == 1


def test_nonresource_explicit_null_fields_are_repaired(tmp_path: Path) -> None:
    client = StubLLMClient(
        {
            "groups": [
                {
                    "group_id": "g1",
                    "represents_resource": False,
                    "canonical_resource_name": None,
                    "identifier_candidate_id": None,
                }
            ]
        },
        _classification(represents_resource=False),
    )
    agent, _catalog = _agent(tmp_path, client)

    result = agent.observe(
        _observation(
            operation_key="GET /health",
            method="GET",
            path="/health",
            body={"status": "ok"},
        )
    )

    assert result.status == "ignored"
    assert len(client.requests) == 2
    repair = __import__("json").loads(client.requests[1].messages[-1].content)
    assert "g1" in " ".join(repair["validation_errors"])


def test_locked_identifier_candidate_cannot_be_replaced_or_omitted(
    tmp_path: Path,
) -> None:
    from restscope.agent.resource_monitor import (
        DetectedResourceGroup,
        MonitoredOperation,
    )

    client = StubLLMClient(
        _classification(
            represents_resource=True,
            canonical_resource_name="user",
            identifier_candidate_id="c2",
        ),
        _classification(
            represents_resource=True,
            canonical_resource_name="user",
            identifier_candidate_id="c1",
        ),
    )
    agent, catalog = _agent(tmp_path, client)
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

    result = agent.observe(
        _observation(
            operation_key="GET /documents/{documentId}",
            method="GET",
            path="/documents/{documentId}",
            body={"owner": {"id": 2, "name": "Grace"}},
        )
    )

    assert result.status == "updated"
    assert len(client.requests) == 2
    repair = __import__("json").loads(client.requests[1].messages[-1].content)
    assert "g1" in " ".join(repair["validation_errors"])


def test_locked_or_matched_group_cannot_be_declared_nonresource(
    tmp_path: Path,
) -> None:
    from restscope.agent.resource_monitor import (
        DetectedResourceGroup,
        MonitoredOperation,
    )

    client = StubLLMClient(
        _classification(represents_resource=False),
        _classification(
            represents_resource=True,
            canonical_resource_name="user",
            identifier_candidate_id="c1",
        ),
    )
    agent, catalog = _agent(tmp_path, client)
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
                resource_aliases=["user", "owner"],
                id_field_name="id",
                id_selector="$.id",
                identifier_values=[1],
                classification_source="exact_id",
            )
        ],
    )

    result = agent.observe(
        _observation(
            operation_key="GET /documents/{documentId}",
            method="GET",
            path="/documents/{documentId}",
            body={"owner": {"userKey": 2}},
        )
    )

    assert result.status == "updated"
    assert len(client.requests) == 2
    repair = __import__("json").loads(client.requests[1].messages[-1].content)
    assert "g1" in " ".join(repair["validation_errors"])


def test_locked_candidate_group_cannot_be_declared_nonresource(
    tmp_path: Path,
) -> None:
    from restscope.agent.resource_monitor import (
        DetectedResourceGroup,
        MonitoredOperation,
    )

    client = StubLLMClient(
        _classification(represents_resource=False),
        _classification(
            represents_resource=True,
            canonical_resource_name="user",
            identifier_candidate_id="c1",
        ),
    )
    agent, catalog = _agent(tmp_path, client)
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

    result = agent.observe(
        _observation(
            operation_key="GET /documents/{documentId}",
            method="GET",
            path="/documents/{documentId}",
            body={"owner": {"id": 2}},
        )
    )

    assert result.status == "updated"
    assert len(client.requests) == 2
    repair = __import__("json").loads(client.requests[1].messages[-1].content)
    assert "g1" in " ".join(repair["validation_errors"])


def test_model_output_requires_complete_unique_group_ids_and_repairs_once(
    tmp_path: Path,
) -> None:
    client = StubLLMClient(
        {
            "groups": [
                {
                    "group_id": "g1",
                    "represents_resource": True,
                    "canonical_resource_name": "user",
                    "identifier_candidate_id": "c1",
                },
                {
                    "group_id": "g1",
                    "represents_resource": True,
                    "canonical_resource_name": "project",
                    "identifier_candidate_id": "c1",
                },
            ]
        },
        {
            "groups": [
                {
                    "group_id": "g1",
                    "represents_resource": True,
                    "canonical_resource_name": "user",
                    "identifier_candidate_id": "c1",
                },
                {
                    "group_id": "g2",
                    "represents_resource": True,
                    "canonical_resource_name": "project",
                    "identifier_candidate_id": "c1",
                },
            ]
        },
    )
    agent, _catalog = _agent(tmp_path, client)

    result = agent.observe(
        _observation(
            operation_key="GET /dashboard",
            method="GET",
            path="/dashboard",
            body={"user": {"userId": 7}, "project": {"projectKey": "p"}},
        )
    )

    assert result.status == "updated"
    assert len(client.requests) == 2
    repair = __import__("json").loads(client.requests[1].messages[-1].content)
    assert len(repair["validation_errors"]) <= 10
    assert "g1" in " ".join(repair["validation_errors"])
    assert "$.user" not in " ".join(repair["validation_errors"])


def test_schema_validation_error_uses_actual_raw_group_id_when_output_is_reordered(
    tmp_path: Path,
) -> None:
    client = StubLLMClient(
        {
            "groups": [
                {
                    "group_id": "g2",
                    "represents_resource": True,
                    "canonical_resource_name": "project",
                    "identifier_candidate_id": "c1",
                    "unexpected": True,
                },
                {
                    "group_id": "g1",
                    "represents_resource": True,
                    "canonical_resource_name": "user",
                    "identifier_candidate_id": "c1",
                },
            ]
        },
        {
            "groups": [
                {
                    "group_id": "g1",
                    "represents_resource": True,
                    "canonical_resource_name": "user",
                    "identifier_candidate_id": "c1",
                },
                {
                    "group_id": "g2",
                    "represents_resource": True,
                    "canonical_resource_name": "project",
                    "identifier_candidate_id": "c1",
                },
            ]
        },
    )
    agent, _catalog = _agent(tmp_path, client)

    result = agent.observe(
        _observation(
            operation_key="GET /dashboard",
            method="GET",
            path="/dashboard",
            body={"user": {"userId": 7}, "project": {"projectKey": "p"}},
        )
    )

    assert result.status == "updated"
    repair = __import__("json").loads(client.requests[1].messages[-1].content)
    errors = " ".join(repair["validation_errors"])
    assert "g2" in errors
    assert "g1" not in errors


def test_non_boolean_represents_resource_is_repaired_with_group_id(
    tmp_path: Path,
) -> None:
    client = StubLLMClient(
        {
            "groups": [
                {
                    "group_id": "g1",
                    "represents_resource": "false",
                }
            ]
        },
        _classification(represents_resource=False),
    )
    agent, _catalog = _agent(tmp_path, client)

    result = agent.observe(
        _observation(
            operation_key="GET /health",
            method="GET",
            path="/health",
            body={"status": "ok"},
        )
    )

    assert result.status == "ignored"
    assert len(client.requests) == 2
    repair = __import__("json").loads(client.requests[1].messages[-1].content)
    assert "g1" in " ".join(repair["validation_errors"])


def test_unknown_candidate_id_is_rejected(tmp_path: Path) -> None:
    import pytest

    from restscope.agent.resource_monitor import ResourceMonitorOutputError

    invalid = _classification(
        represents_resource=True,
        canonical_resource_name="commit",
        identifier_candidate_id="c99",
    )
    client = StubLLMClient(invalid, invalid)
    agent, _catalog = _agent(tmp_path, client)

    with pytest.raises(ResourceMonitorOutputError) as raised:
        agent.observe(
            _observation(body={"sha": "abc123"})
        )

    assert raised.value.code == "resource_monitor_output_invalid"


def test_missing_group_is_repaired_without_an_unknown_candidate(tmp_path: Path) -> None:
    import pytest

    from restscope.agent.resource_monitor import ResourceMonitorOutputError

    incomplete = _classification(
        represents_resource=True,
        canonical_resource_name="commit",
        identifier_candidate_id="c1",
    )
    client = StubLLMClient(incomplete, incomplete)
    agent, _catalog = _agent(tmp_path, client)

    with pytest.raises(ResourceMonitorOutputError) as raised:
        agent.observe(
            _observation(
                operation_key="GET /dashboard",
                method="GET",
                path="/dashboard",
                body={"commit": {"sha": "abc123"}, "project": {"key": "p"}},
            )
        )

    assert raised.value.code == "resource_monitor_output_invalid"


def test_prompt_excludes_non_identifier_scalar_types(tmp_path: Path) -> None:
    client = StubLLMClient(
        _classification(
            represents_resource=True,
            canonical_resource_name="commit",
            identifier_candidate_id="c1",
        )
    )
    agent, _catalog = _agent(tmp_path, client)

    agent.observe(
        _observation(
            operation_key="POST /commits",
            path="/commits",
            body={"sha": "abc123", "active": True, "ratio": 1.5, "none": None},
        )
    )

    payload = __import__("json").loads(client.requests[0].messages[1].content)
    candidates = payload["groups"][0]["identifier_candidates"]
    assert [item["field_path"] for item in candidates] == ["sha"]


def test_prompt_excludes_candidates_with_mixed_valid_and_invalid_types(
    tmp_path: Path,
) -> None:
    client = StubLLMClient(
        _classification(
            represents_resource=True,
            canonical_resource_name="commit",
            identifier_candidate_id="c1",
        )
    )
    agent, _catalog = _agent(tmp_path, client)
    observation = _observation(
        operation_key="POST /commits",
        path="/commits",
        body={"mixed": "value", "sha": "abc123"},
    ).model_copy(
        update={
            "response_schema_fields": [
                {
                    "selector": "$.mixed",
                    "name": "mixed",
                    "type": ["string", "boolean"],
                }
            ]
        }
    )

    result = agent.observe(observation)

    assert result.status == "updated"
    payload = __import__("json").loads(client.requests[0].messages[1].content)
    assert [
        item["field_path"] for item in payload["groups"][0]["identifier_candidates"]
    ] == ["sha"]


def test_identifier_candidate_limits_fail_closed(tmp_path: Path) -> None:
    client = StubLLMClient()
    agent, _catalog = _agent(tmp_path, client)

    per_group = agent.observe(
        _observation(body={f"field_{index}": str(index) for index in range(21)})
    )
    total = agent.observe(
        _observation(
            operation_key="GET /many",
            method="GET",
            path="/many",
            body={
                f"group_{group_index}": {
                    f"field_{field_index}": str(field_index)
                    for field_index in range(17)
                }
                for group_index in range(6)
            },
        )
    )

    assert per_group.status == "warning"
    assert per_group.warning is not None
    assert per_group.warning.code == "resource_monitor_evidence_limit_exceeded"
    assert total.status == "warning"
    assert total.warning is not None
    assert total.warning.code == "resource_monitor_evidence_limit_exceeded"
    assert client.requests == []


def test_builder_selects_configured_fast_model(tmp_path: Path) -> None:
    from restscope.agent.resource_monitor import build_resource_monitor_agent
    from restscope.restscope_config import RESTScopeConfig

    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "THINK_MODEL=thinking-model",
                "FAST_MODEL=fast-model",
                f"DB_URL=sqlite:///{tmp_path / 'factory.sqlite'}",
            ]
        ),
        encoding="utf-8",
    )
    client = StubLLMClient()

    agent = build_resource_monitor_agent(
        RESTScopeConfig.from_environment(env_file),
        llm_client=client,
    )

    assert agent.client is client
    assert agent.model.role == "resource_monitor"
    assert agent.model.model == "fast-model"


def test_resource_lookup_tool_returns_complete_structured_result(
    tmp_path: Path,
) -> None:
    from restscope.agent.resource_monitor import (
        DetectedResourceGroup,
        MonitoredOperation,
        register_resource_lookup_tool,
    )
    from restscope.capabilities import (
        ToolCallValidator,
        ToolContext,
        ToolExecutor,
        ToolPolicy,
        ToolRegistry,
    )
    from restscope.llm import ToolCall
    from restscope.openapi_parser import OpenAPIParser

    agent, catalog = _agent(tmp_path, StubLLMClient())
    catalog.record_groups(
        operation=MonitoredOperation(
            operation_key="GET /users/{userId}",
            method="GET",
            path="/users/{userId}",
        ),
        groups=[
            DetectedResourceGroup(
                group_path="$",
                resource_name="user",
                resource_aliases=["user"],
                id_field_name="id",
                id_selector="$.id",
                identifier_values=[9],
                classification_source="exact_id",
            )
        ],
    )
    registry = ToolRegistry()
    spec = register_resource_lookup_tool(registry, agent)
    executor = ToolExecutor(
        registry,
        ToolCallValidator(registry, ToolPolicy()),
    )
    executor.bind_context(
        ToolContext(
            ir=OpenAPIParser.parse(
                {
                    "openapi": "3.0.3",
                    "info": {"title": "lookup", "version": "1"},
                    "paths": {},
                }
            ),
            baseline_schema_source={
                "kind": "inline",
                "format": "json",
                "content": "{}",
            },
        )
    )

    result = executor.execute(
        tool_call=ToolCall(
            id="lookup",
            name="restscope.resource.lookup",
            arguments={"resource": "user"},
        ),
        role="operation_tester",
        state={},
    )

    assert spec.kind == "local_function"
    assert spec.read_only is True
    assert spec.risk_level == "medium"
    assert spec.requires_approval is False
    assert result.status == "succeeded"
    assert result.structured["recommended_id"] == 9
    assert result.structured["operations"][0]["operation_key"] == (
        "GET /users/{userId}"
    )


def test_schema_only_identifier_rule_is_learned_then_reused_when_value_appears(
    tmp_path: Path,
) -> None:
    from restscope.agent.resource_monitor import ResourceLookupRequest

    client = StubLLMClient(
        _classification(
            represents_resource=True,
            canonical_resource_name="commit",
            identifier_candidate_id="c2",
        )
    )
    agent, catalog = _agent(tmp_path, client)
    observation = _observation(
        operation_key="GET /commits/{commitId}",
        method="GET",
        path="/commits/{commitId}",
        body={"message": "identifier omitted"},
    ).model_copy(
        update={
            "response_schema_fields": [
                {
                    "selector": "$.sha",
                    "name": "sha",
                    "path_segments": ["sha"],
                    "type": "string",
                    "format": "sha1",
                    "description": "Canonical commit hash",
                    "required": False,
                }
            ]
        }
    )

    first = agent.observe(observation)
    second = agent.observe(
        observation.model_copy(update={"body": {"sha": "abc123"}})
    )

    assert first.status == "updated"
    assert first.identifiers_recorded == 0
    assert second.status == "updated"
    assert len(client.requests) == 1
    prompt = "\n".join(message.content for message in client.requests[0].messages)
    assert "Canonical commit hash" in prompt
    assert '"schema_format": "sha1"' in prompt
    assert "response_schema_fields" not in prompt
    assert "path_segments" not in prompt
    assert catalog.lookup(
        ResourceLookupRequest(resource="commit")
    ).recommended_id == "abc123"


def test_oversized_schema_format_fails_closed_without_llm(tmp_path: Path) -> None:
    client = StubLLMClient()
    agent, _catalog = _agent(tmp_path, client)
    observation = _observation(body={"sha": "abc123"}).model_copy(
        update={
            "response_schema_fields": [
                {
                    "selector": "$.sha",
                    "name": "sha",
                    "type": "string",
                    "format": "x" * 201,
                }
            ]
        }
    )

    result = agent.observe(observation)

    assert result.status == "warning"
    assert result.warning is not None
    assert result.warning.code == "resource_monitor_evidence_limit_exceeded"
    assert client.requests == []


def test_empty_candidate_group_is_cached_without_llm_even_when_name_is_known(
    tmp_path: Path,
) -> None:
    from restscope.agent.resource_monitor import (
        DetectedResourceGroup,
        MonitoredOperation,
    )

    client = StubLLMClient()
    agent, catalog = _agent(tmp_path, client)
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
                resource_aliases=["user", "owner"],
                id_field_name="id",
                id_selector="$.id",
                identifier_values=[1],
                classification_source="exact_id",
            )
        ],
    )
    observation = _observation(
        operation_key="GET /documents/{documentId}",
        method="GET",
        path="/documents/{documentId}",
        body={"owner": {"active": True, "ratio": 1.5}},
    )

    assert agent.observe(observation).status == "ignored"
    second_observation = observation.model_copy(
        update={"body": {"owner": {"active": False, "ratio": 2.5}}}
    )
    assert agent.observe(second_observation).status == "ignored"
    assert client.requests == []
    assert catalog.list_rules("GET /documents/{documentId}")[0].has_resource is False


def test_reserved_response_key_fails_closed_without_catalog_pollution(
    tmp_path: Path,
) -> None:
    from restscope.agent.resource_monitor import ResourceLookupRequest

    client = StubLLMClient()
    agent, catalog = _agent(tmp_path, client)
    observation = _observation(body={"bad.key": "abc123"})

    first = agent.observe(observation)
    second = agent.observe(observation)

    assert first.status == "warning"
    assert second.status == "warning"
    assert client.requests == []
    assert catalog.list_rules("POST /users") == []
    assert catalog.lookup(ResourceLookupRequest(resource="user")).status == "not_found"


def test_first_observation_uses_loaded_catalog_context_without_lookup_calls(
    tmp_path: Path,
) -> None:
    from restscope.agent.resource_monitor import (
        DetectedResourceGroup,
        MonitoredOperation,
    )

    client = StubLLMClient(
        _classification(
            represents_resource=True,
            canonical_resource_name="user",
            identifier_candidate_id="c1",
        )
    )
    agent, catalog = _agent(tmp_path, client)
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
                resource_aliases=["user", "owner"],
                id_field_name="id",
                id_selector="$.id",
                identifier_values=[1],
                classification_source="exact_id",
            )
        ],
    )

    def lookup_must_not_be_called(*_args, **_kwargs):
        raise AssertionError("first-observation catalog context must not call lookup")

    catalog.lookup = lookup_must_not_be_called
    result = agent.observe(
        _observation(
            operation_key="GET /documents/{documentId}",
            method="GET",
            path="/documents/{documentId}",
            body={"owner": {"userKey": 2}},
        )
    )

    assert result.status == "updated"


def test_first_observation_requests_bounded_alias_window_from_catalog(
    tmp_path: Path,
) -> None:
    client = StubLLMClient(
        _classification(
            represents_resource=True,
            canonical_resource_name="commit",
            identifier_candidate_id="c1",
        )
    )
    agent, catalog = _agent(tmp_path, client)
    original_list_resources = catalog.list_resources
    calls: list[dict[str, int | None]] = []

    def capture_list_resources(*, limit=None, aliases_per_resource=None):
        calls.append(
            {
                "limit": limit,
                "aliases_per_resource": aliases_per_resource,
            }
        )
        return original_list_resources(
            limit=limit,
            aliases_per_resource=aliases_per_resource,
        )

    catalog.list_resources = capture_list_resources
    result = agent.observe(
        _observation(
            operation_key="POST /commits",
            path="/commits",
            body={"sha": "abc123"},
        )
    )

    assert result.status == "updated"
    assert calls == [{"limit": 101, "aliases_per_resource": 21}]


def test_invalid_model_selector_is_repaired_once(tmp_path: Path) -> None:
    from restscope.agent.resource_monitor import ResourceLookupRequest

    client = StubLLMClient(
        _classification(
            represents_resource=True,
            canonical_resource_name="commit",
            identifier_candidate_id="c99",
        ),
        _classification(
            represents_resource=True,
            canonical_resource_name="commit",
            identifier_candidate_id="c1",
        ),
    )
    agent, catalog = _agent(tmp_path, client)

    result = agent.observe(
        _observation(
            operation_key="POST /commits",
            path="/commits",
            body={"sha": "abc123"},
        )
    )

    assert result.status == "updated"
    assert len(client.requests) == 2
    assert "unknown candidate id c99" in client.requests[1].messages[-1].content
    assert catalog.lookup(
        ResourceLookupRequest(resource="commit")
    ).recommended_id == "abc123"


def test_model_generated_aliases_are_not_accepted_or_persisted(
    tmp_path: Path,
) -> None:
    from restscope.agent.resource_monitor import ResourceLookupRequest

    client = StubLLMClient(
        {
            "groups": [
                {
                    "group_id": "g1",
                    "represents_resource": True,
                    "canonical_resource_name": "commit",
                    "identifier_candidate_id": "c1",
                    "resource_aliases": ["revision"],
                }
            ]
        },
        _classification(
            represents_resource=True,
            canonical_resource_name="commit",
            identifier_candidate_id="c1",
        ),
    )
    agent, catalog = _agent(tmp_path, client)

    result = agent.observe(
        _observation(
            operation_key="POST /commits",
            path="/commits",
            body={"sha": "abc123"},
        )
    )

    assert result.status == "updated"
    assert len(client.requests) == 2
    repair = __import__("json").loads(client.requests[1].messages[-1].content)
    assert "g1" in " ".join(repair["validation_errors"])
    assert "groups.0" not in " ".join(repair["validation_errors"])
    assert catalog.lookup(
        ResourceLookupRequest(resource="commit")
    ).recommended_id == "abc123"
    assert (
        catalog.lookup(ResourceLookupRequest(resource="revision")).status
        == "not_found"
    )


def test_model_output_rejects_unrecognized_fields_before_persistence(
    tmp_path: Path,
) -> None:
    client = StubLLMClient(
        {
            "groups": [
                {
                    "group_id": "g1",
                    "represents_resource": True,
                    "canonical_resource_name": "commit",
                    "identifier_candidate_id": "c1",
                    "resource_aliases": [f"alias-{index}" for index in range(20)],
                }
            ]
        },
        _classification(
            represents_resource=True,
            canonical_resource_name="commit",
            identifier_candidate_id="c1",
        ),
    )
    agent, _catalog = _agent(tmp_path, client)

    result = agent.observe(
        _observation(
            operation_key="POST /commits",
            path="/commits",
            body={"sha": "abc123"},
        )
    )

    assert result.status == "updated"
    assert len(client.requests) == 2


def test_identifier_cardinality_limit_warns_without_partial_persistence(
    tmp_path: Path,
) -> None:
    from restscope.agent.resource_monitor import ResourceLookupRequest

    client = StubLLMClient()
    agent, catalog = _agent(tmp_path, client)

    result = agent.observe(
        _observation(
            operation_key="GET /users",
            method="GET",
            path="/users",
            body=[{"id": value} for value in range(1001)],
        )
    )

    assert result.status == "warning"
    assert result.warning is not None
    assert result.warning.code == "resource_monitor_evidence_limit_exceeded"
    assert client.requests == []
    assert catalog.lookup(ResourceLookupRequest(resource="user")).status == (
        "not_found"
    )


def test_oversized_response_field_name_warns_before_model_call(
    tmp_path: Path,
) -> None:
    client = StubLLMClient()
    agent, _catalog = _agent(tmp_path, client)

    result = agent.observe(
        _observation(body={"x" * 201: "value"})
    )

    assert result.status == "warning"
    assert result.warning is not None
    assert result.warning.code == "resource_monitor_evidence_limit_exceeded"
    assert client.requests == []


def test_oversized_first_identifier_returns_bounded_warning(
    tmp_path: Path,
) -> None:
    from restscope.agent.resource_monitor import ResourceLookupRequest

    agent, catalog = _agent(tmp_path, StubLLMClient())

    result = agent.observe(
        _observation(body={"id": "x" * 4097})
    )

    assert result.status == "warning"
    assert result.warning is not None
    assert result.warning.code == "resource_monitor_evidence_limit_exceeded"
    assert catalog.lookup(ResourceLookupRequest(resource="user")).status == (
        "not_found"
    )


def test_existing_resource_model_context_fails_closed_before_truncation() -> None:
    import pytest

    from restscope.agent.resource_monitor.agent import (
        _EvidenceLimitExceeded,
        _existing_resource_prompt,
    )
    from restscope.agent.resource_monitor.schemas import ResourceNameSummary

    with pytest.raises(_EvidenceLimitExceeded):
        _existing_resource_prompt(
            [
                ResourceNameSummary(
                    resource_id=f"resource_{index}",
                    canonical_name=f"resource-{index}",
                    aliases=[f"alias-{index}"],
                )
                for index in range(101)
            ]
        )


def test_existing_resource_context_rejects_invalid_canonical_or_alias_data() -> None:
    import pytest

    from restscope.agent.resource_monitor.agent import (
        _EvidenceLimitExceeded,
        _resource_prompt_context,
    )
    from restscope.agent.resource_monitor.schemas import ResourceNameSummary

    invalid_contexts = [
        [
            ResourceNameSummary(
                resource_id="resource",
                canonical_name="x" * 201,
                aliases=["resource"],
            )
        ],
        [
            ResourceNameSummary(
                resource_id="resource",
                canonical_name="resource",
                aliases=["x" * 201],
            )
        ],
        [
            ResourceNameSummary(
                resource_id="resource",
                canonical_name="resource",
                aliases=[f"alias-{index}" for index in range(21)],
            )
        ],
    ]

    for resources in invalid_contexts:
        with pytest.raises(_EvidenceLimitExceeded):
            _resource_prompt_context(resources)


def test_two_invalid_model_outputs_do_not_persist_partial_rules(
    tmp_path: Path,
) -> None:
    import pytest

    from restscope.agent.resource_monitor import (
        ResourceLookupRequest,
        ResourceMonitorOutputError,
    )

    invalid = _classification(
        represents_resource=True,
        canonical_resource_name="commit",
        identifier_candidate_id="c99",
    )
    client = StubLLMClient(invalid, invalid)
    agent, catalog = _agent(tmp_path, client)

    with pytest.raises(ResourceMonitorOutputError) as raised:
        agent.observe(
            _observation(
                operation_key="POST /commits",
                path="/commits",
                body={"sha": "abc123"},
            )
        )

    assert raised.value.code == "resource_monitor_output_invalid"
    assert catalog.list_rules("POST /commits") == []
    assert catalog.lookup(ResourceLookupRequest(resource="commit")).status == (
        "not_found"
    )
