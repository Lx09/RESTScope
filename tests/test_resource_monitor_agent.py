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


def _selection(identifier_candidate_id: str | None) -> dict:
    return {"identifier": identifier_candidate_id}


def _catalog(tmp_path: Path):
    from restscope.agent.api_behavior_monitor import ResourceCatalog
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
    from restscope.agent.api_behavior_monitor import ResourceIdentifierTracker
    from restscope.llm import LLMModelConfig

    catalog = _catalog(tmp_path)
    tracker = ResourceIdentifierTracker(
        catalog=catalog,
        client=client,
        model=LLMModelConfig(
            role="api_behavior_monitor",
            provider="stub",
            model="fast-stub",
        ),
    )
    return tracker, catalog


def _observation(
    *,
    operation_key: str = "POST /users",
    method: str = "POST",
    path: str = "/users",
    body,
):
    from restscope.agent.api_behavior_monitor import (
        MonitoredOperation,
        ResourceObservation,
    )

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


def _record_user_resource(catalog, *, aliases: list[str] | None = None) -> None:
    from restscope.agent.api_behavior_monitor import (
        DetectedResourceGroup,
        MonitoredOperation,
    )

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
                resource_aliases=aliases or ["user"],
                id_field_name="id",
                id_selector="$.id",
                identifier_values=[1],
                classification_source="exact_id",
            )
        ],
    )


def test_exact_id_is_recorded_without_calling_llm(tmp_path: Path) -> None:
    from restscope.agent.api_behavior_monitor import ResourceLookupRequest

    client = StubLLMClient()
    tracker, catalog = _agent(tmp_path, client)

    result = tracker.observe(_observation(body={"id": 42, "name": "Ada"}))

    assert result.status == "updated"
    assert result.identifiers_recorded == 1
    assert client.requests == []
    lookup = catalog.lookup(ResourceLookupRequest(resource="user"))
    assert lookup.recommended_id == 42
    assert lookup.operations[0].id_field_aliases == ["id"]


def test_exact_id_wins_over_other_id_suffix_fields(tmp_path: Path) -> None:
    from restscope.agent.api_behavior_monitor import ResourceLookupRequest

    client = StubLLMClient()
    tracker, catalog = _agent(tmp_path, client)

    result = tracker.observe(
        _observation(body={"userId": 7, "id": 42, "project_id": 9})
    )

    assert result.status == "updated"
    assert client.requests == []
    lookup = catalog.lookup(ResourceLookupRequest(resource="user", limit=100))
    assert [item.value for item in lookup.identifiers] == [42]


def test_semantic_identifier_prompt_is_minimal_and_hides_values(
    tmp_path: Path,
) -> None:
    from restscope.agent.api_behavior_monitor import ResourceLookupRequest

    client = StubLLMClient(_selection("I1"))
    tracker, catalog = _agent(tmp_path, client)

    result = tracker.observe(
        _observation(
            operation_key="POST /commits",
            path="/commits",
            body={"sha": "secret-abc123", "message": "initial"},
        )
    )

    assert result.status == "updated"
    assert len(client.requests) == 1
    request = client.requests[0]
    assert request.metadata["role"] == "api_behavior_monitor"
    prompt = request.messages[1].content
    assert "Operation\nPOST /commits" in prompt
    assert 'Resource\n"commit"' in prompt
    assert 'Response section\n[G1] root' in prompt
    assert '[I1] field "sha"; type=string; observed=yes' in prompt
    assert '[I2] field "message"; type=string; observed=yes' in prompt
    assert request.response_format == "json"
    assert request.json_schema is None
    for forbidden in (
        "secret-abc123",
        "operation_key",
        "selector",
        "resource_id",
        "aliases",
        "known_resource_names",
        "represents_resource",
        "identifier_candidate_id",
        "$defs",
    ):
        assert forbidden not in prompt
    assert catalog.lookup(
        ResourceLookupRequest(resource="commit")
    ).recommended_id == "secret-abc123"


def test_non_exact_id_suffix_candidates_are_preferred_but_require_llm(
    tmp_path: Path,
) -> None:
    client = StubLLMClient(_selection("I1"))
    tracker, _catalog = _agent(tmp_path, client)

    result = tracker.observe(
        _observation(
            body={"sha": "abc123", "userId": 7, "message": "initial"},
        )
    )

    assert result.status == "updated"
    prompt = client.requests[0].messages[1].content
    assert '[I1] field "userId"' in prompt
    assert 'field "sha"' not in prompt


def test_prompt_excludes_invalid_scalar_types_and_mixed_schema_types(
    tmp_path: Path,
) -> None:
    client = StubLLMClient(_selection("I1"))
    tracker, _catalog = _agent(tmp_path, client)
    observation = _observation(
        operation_key="POST /commits",
        path="/commits",
        body={
            "mixed": "value",
            "sha": "abc123",
            "active": True,
            "ratio": 1.5,
            "none": None,
        },
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

    assert tracker.observe(observation).status == "updated"
    prompt = client.requests[0].messages[1].content
    assert '[I1] field "sha"' in prompt
    assert 'field "mixed"' not in prompt


def test_invalid_exact_id_type_does_not_hide_a_valid_semantic_candidate(
    tmp_path: Path,
) -> None:
    client = StubLLMClient(_selection("I1"))
    tracker, _catalog = _agent(tmp_path, client)

    result = tracker.observe(
        _observation(
            operation_key="POST /commits",
            path="/commits",
            body={"id": True, "sha": "abc123"},
        )
    )

    assert result.status == "updated"
    prompt = client.requests[0].messages[1].content
    assert '[I1] field "sha"' in prompt
    assert 'field "id"' not in prompt


def test_semantic_identifier_uses_two_stable_batches_of_50_candidates(
    tmp_path: Path,
) -> None:
    client = StubLLMClient(_selection(None), _selection("I51"))
    tracker, _catalog = _agent(tmp_path, client)
    body = {f"field{index}": f"value-{index}" for index in range(51)}

    result = tracker.observe(_observation(body=body))

    assert result.status == "updated"
    assert len(client.requests) == 2
    first = client.requests[0].messages[1].content
    second = client.requests[1].messages[1].content
    for index in range(1, 51):
        assert f"[I{index}]" in first
    assert "[I51] field \"field50\"; type=string; observed=yes" in second
    assert "[I50]" not in second


def test_semantic_identifier_ignores_candidates_after_first_100(
    tmp_path: Path,
) -> None:
    client = StubLLMClient(_selection(None), _selection(None))
    tracker, catalog = _agent(tmp_path, client)
    body = {f"field{index}": f"value-{index}" for index in range(101)}

    result = tracker.observe(_observation(body=body))

    assert result.status == "ignored"
    assert len(client.requests) == 2
    rendered = "\n".join(
        message.content
        for request in client.requests
        for message in request.messages
    )
    assert '"field99"' in rendered
    assert '"field100"' not in rendered
    assert catalog.list_rules("POST /users") == []


def test_invalid_first_selection_uses_second_and_final_call_for_repair(
    tmp_path: Path,
) -> None:
    client = StubLLMClient(_selection("forged"), _selection("I1"))
    tracker, _catalog = _agent(tmp_path, client)
    body = {f"field{index}": f"value-{index}" for index in range(51)}

    result = tracker.observe(_observation(body=body))

    assert result.status == "updated"
    assert len(client.requests) == 2
    repair = client.requests[1].messages[-1].content
    assert "Your previous JSON could not be used." in repair
    assert "forged was not offered" in repair
    assert "I1" in repair


def test_two_invalid_model_outputs_do_not_persist_partial_rules(
    tmp_path: Path,
) -> None:
    import pytest

    from restscope.agent.api_behavior_monitor import (
        ResourceIdentifierOutputError,
        ResourceLookupRequest,
    )

    client = StubLLMClient(_selection("forged"), _selection("forged"))
    tracker, catalog = _agent(tmp_path, client)

    with pytest.raises(ResourceIdentifierOutputError) as raised:
        tracker.observe(
            _observation(
                operation_key="POST /commits",
                path="/commits",
                body={"sha": "abc123"},
            )
        )

    assert raised.value.code == "resource_monitor_output_invalid"
    assert len(client.requests) == 2
    assert catalog.list_rules("POST /commits") == []
    assert catalog.lookup(ResourceLookupRequest(resource="commit")).status == (
        "not_found"
    )


def test_extra_model_fields_are_repaired_and_not_persisted(
    tmp_path: Path,
) -> None:
    from restscope.agent.api_behavior_monitor import ResourceLookupRequest

    client = StubLLMClient(
        {
            "identifier_candidate_id": "c1",
            "aliases": ["revision"],
        },
        _selection("I1"),
    )
    tracker, catalog = _agent(tmp_path, client)

    result = tracker.observe(
        _observation(
            operation_key="POST /commits",
            path="/commits",
            body={"sha": "abc123"},
        )
    )

    assert result.status == "updated"
    assert len(client.requests) == 2
    repair = client.requests[1].messages[-1].content
    assert "only the identifier field" in repair
    assert catalog.lookup(ResourceLookupRequest(resource="revision")).status == (
        "not_found"
    )


def test_null_identifier_selection_is_retried_without_negative_rule(
    tmp_path: Path,
) -> None:
    client = StubLLMClient(_selection(None), _selection(None))
    tracker, catalog = _agent(tmp_path, client)
    observation = _observation(
        operation_key="GET /health",
        method="GET",
        path="/health",
        body={"status": "ok"},
    )

    assert tracker.observe(observation).status == "ignored"
    assert tracker.observe(observation).status == "ignored"
    assert len(client.requests) == 2
    assert catalog.list_rules("GET /health") == []


def test_learned_rule_is_reused_and_missing_identifier_returns_warning(
    tmp_path: Path,
) -> None:
    from restscope.agent.api_behavior_monitor import ResourceLookupRequest

    client = StubLLMClient(_selection("I1"))
    tracker, catalog = _agent(tmp_path, client)
    first = _observation(
        operation_key="GET /commits/{commitId}",
        method="GET",
        path="/commits/{commitId}",
        body={"sha": "first"},
    )

    assert tracker.observe(first).status == "updated"
    assert tracker.observe(
        first.model_copy(update={"body": {"sha": "second"}})
    ).status == "updated"
    missing = tracker.observe(
        first.model_copy(update={"body": {"message": "missing"}})
    )

    assert len(client.requests) == 1
    assert missing.status == "warning"
    assert missing.warning is not None
    assert missing.warning.code == "expected_resource_id_missing"
    lookup = catalog.lookup(ResourceLookupRequest(resource="commit"))
    assert [item.value for item in lookup.identifiers] == ["second", "first"]
    assert [error.code for error in lookup.errors] == [
        "expected_resource_id_missing"
    ]


def test_schema_only_semantic_identifier_retries_until_value_is_observed(
    tmp_path: Path,
) -> None:
    from restscope.agent.api_behavior_monitor import ResourceLookupRequest

    client = StubLLMClient(_selection("I2"), _selection("I1"))
    tracker, catalog = _agent(tmp_path, client)
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
                }
            ]
        }
    )

    first = tracker.observe(observation)
    assert first.status == "warning"
    assert first.warning is not None
    assert first.warning.code == "expected_resource_id_missing"
    assert catalog.list_rules("GET /commits/{commitId}") == []
    second = tracker.observe(
        observation.model_copy(update={"body": {"sha": "abc123"}})
    )
    assert second.status == "updated"
    assert len(client.requests) == 2
    prompt = "\n".join(
        message.content
        for request in client.requests
        for message in request.messages
    )
    assert "Canonical commit hash" in prompt
    assert "format=sha1" in prompt
    assert catalog.lookup(
        ResourceLookupRequest(resource="commit")
    ).recommended_id == "abc123"


def test_schema_only_exact_id_warns_without_rule_or_llm(tmp_path: Path) -> None:
    from restscope.agent.api_behavior_monitor import ResourceLookupRequest

    client = StubLLMClient()
    tracker, catalog = _agent(tmp_path, client)
    observation = _observation(body={"name": "Ada"}).model_copy(
        update={
            "response_schema_fields": [
                {
                    "selector": "$.id",
                    "name": "id",
                    "path_segments": ["id"],
                    "type": "integer",
                    "resource_name": "User",
                }
            ]
        }
    )

    result = tracker.observe(observation)

    assert result.status == "warning"
    assert result.warning is not None
    assert result.warning.code == "expected_resource_id_missing"
    assert client.requests == []
    assert catalog.list_rules("POST /users") == []
    assert catalog.lookup(ResourceLookupRequest(resource="user")).status == (
        "not_found"
    )


def test_wrapped_collection_items_are_one_resource_group(
    tmp_path: Path,
) -> None:
    from restscope.agent.api_behavior_monitor import ResourceLookupRequest

    client = StubLLMClient()
    tracker, catalog = _agent(tmp_path, client)

    result = tracker.observe(
        _observation(
            operation_key="GET /app/api/assignments",
            method="GET",
            path="/app/api/assignments",
            body={
                "collection": [
                    {"id": 1, "owner": {"id": 71}},
                    {"id": 2, "owner": {"id": 72}},
                ],
                "total": 2,
            },
        )
    )

    assert result.status == "updated"
    assert result.groups_processed == 1
    assert result.identifiers_recorded == 2
    assert client.requests == []
    lookup = catalog.lookup(
        ResourceLookupRequest(resource="assignment", limit=100)
    )
    assert {item.value for item in lookup.identifiers} == {1, 2}
    assert catalog.lookup(ResourceLookupRequest(resource="collection")).status == (
        "not_found"
    )
    assert [
        (item.group_path, item.id_selector)
        for item in catalog.list_rules("GET /app/api/assignments")
    ] == [("$.collection[]", "$.collection[].id")]


def test_generic_wrapper_uses_schema_resource_name_without_llm(
    tmp_path: Path,
) -> None:
    from restscope.agent.api_behavior_monitor import ResourceLookupRequest

    client = StubLLMClient()
    tracker, catalog = _agent(tmp_path, client)
    observation = _observation(
        operation_key="GET /dashboard",
        method="GET",
        path="/dashboard",
        body={"data": [{"id": 9, "label": "ready"}]},
    ).model_copy(
        update={
            "response_schema_fields": [
                {
                    "selector": "$.data[].id",
                    "name": "id",
                    "path_segments": ["data", "id"],
                    "type": "integer",
                    "resource_name": "Assignment",
                }
            ]
        }
    )

    assert tracker.observe(observation).status == "updated"
    assert client.requests == []
    assert catalog.lookup(
        ResourceLookupRequest(resource="assignment")
    ).recommended_id == 9
    assert catalog.lookup(ResourceLookupRequest(resource="data")).status == (
        "not_found"
    )


def test_existing_alias_resolves_canonical_name_locally(tmp_path: Path) -> None:
    from restscope.agent.api_behavior_monitor import ResourceLookupRequest

    client = StubLLMClient(_selection("I1"))
    tracker, catalog = _agent(tmp_path, client)
    _record_user_resource(catalog, aliases=["user", "owner"])

    result = tracker.observe(
        _observation(
            operation_key="GET /owners",
            method="GET",
            path="/owners",
            body={"userKey": 2},
        )
    )

    assert result.status == "updated"
    prompt = client.requests[0].messages[1].content
    assert 'Resource\n"user"' in prompt
    lookup = catalog.lookup(ResourceLookupRequest(resource="owner", limit=100))
    assert lookup.canonical_resource == "user"
    assert {item.value for item in lookup.identifiers} == {1, 2}


def test_collection_truncates_after_1000_and_persists_first_1000(
    tmp_path: Path,
) -> None:
    from restscope.agent.api_behavior_monitor import ResourceLookupRequest

    tracker, catalog = _agent(tmp_path, StubLLMClient())

    result = tracker.observe(
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
    assert result.identifiers_recorded == 1000
    lookup = catalog.lookup(ResourceLookupRequest(resource="user", limit=100))
    assert lookup.total == 1000
    assert {item.value for item in lookup.identifiers}.issubset(set(range(1000)))


def test_oversized_collection_item_is_skipped_without_losing_other_ids(
    tmp_path: Path,
) -> None:
    from restscope.agent.api_behavior_monitor import ResourceLookupRequest

    tracker, catalog = _agent(tmp_path, StubLLMClient())

    result = tracker.observe(
        _observation(
            operation_key="GET /users",
            method="GET",
            path="/users",
            body=[
                {"id": 1, "payload": list(range(1001))},
                {"id": 2, "name": "usable"},
            ],
        )
    )

    assert result.status == "warning"
    assert result.warning is not None
    assert result.warning.code == "resource_monitor_evidence_limit_exceeded"
    lookup = catalog.lookup(ResourceLookupRequest(resource="user", limit=100))
    assert [item.value for item in lookup.identifiers] == [2]


def test_learned_collection_rule_saves_present_ids_and_warns_for_missing(
    tmp_path: Path,
) -> None:
    from restscope.agent.api_behavior_monitor import ResourceLookupRequest

    tracker, catalog = _agent(tmp_path, StubLLMClient())
    first = _observation(
        operation_key="GET /users",
        method="GET",
        path="/users",
        body=[{"id": 1}, {"id": 2}],
    )

    assert tracker.observe(first).status == "updated"
    second = tracker.observe(
        first.model_copy(update={"body": [{"id": 3}, {"name": "missing"}]})
    )

    assert second.status == "warning"
    assert second.warning is not None
    assert second.warning.code == "expected_resource_id_missing"
    assert "$[1]" in second.warning.issues
    lookup = catalog.lookup(ResourceLookupRequest(resource="user", limit=100))
    assert {item.value for item in lookup.identifiers} == {1, 2, 3}


def test_oversized_schema_format_fails_closed_without_llm(
    tmp_path: Path,
) -> None:
    client = StubLLMClient()
    tracker, _catalog = _agent(tmp_path, client)
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

    result = tracker.observe(observation)

    assert result.status == "warning"
    assert result.warning is not None
    assert result.warning.code == "resource_monitor_evidence_limit_exceeded"
    assert client.requests == []


def test_invalid_response_field_names_fail_without_catalog_pollution(
    tmp_path: Path,
) -> None:
    from restscope.agent.api_behavior_monitor import ResourceLookupRequest

    client = StubLLMClient()
    tracker, catalog = _agent(tmp_path, client)
    for body in ({"bad.key": "value"}, {"x" * 201: "value"}):
        result = tracker.observe(_observation(body=body))
        assert result.status == "warning"
        assert result.warning is not None
        assert result.warning.code == "resource_monitor_evidence_limit_exceeded"

    assert client.requests == []
    assert catalog.list_rules("POST /users") == []
    assert catalog.lookup(ResourceLookupRequest(resource="user")).status == (
        "not_found"
    )


def test_oversized_identifier_returns_bounded_warning(tmp_path: Path) -> None:
    from restscope.agent.api_behavior_monitor import ResourceLookupRequest

    tracker, catalog = _agent(tmp_path, StubLLMClient())

    result = tracker.observe(_observation(body={"id": "x" * 4097}))

    assert result.status == "warning"
    assert result.warning is not None
    assert result.warning.code == "resource_monitor_evidence_limit_exceeded"
    assert catalog.lookup(ResourceLookupRequest(resource="user")).status == (
        "not_found"
    )


def test_legacy_negative_rule_is_replaced_by_positive_evidence(
    tmp_path: Path,
) -> None:
    from restscope.agent.api_behavior_monitor import (
        DetectedResourceGroup,
        MonitoredOperation,
        ResourceLookupRequest,
    )

    tracker, catalog = _agent(tmp_path, StubLLMClient())
    operation = MonitoredOperation(
        operation_key="GET /users",
        method="GET",
        path="/users",
    )
    catalog.record_groups(
        operation=operation,
        groups=[
            DetectedResourceGroup(
                group_path="$",
                has_resource=False,
                classification_source="llm",
            )
        ],
    )

    result = tracker.observe(
        _observation(
            operation_key=operation.operation_key,
            method=operation.method,
            path=operation.path,
            body={"id": 9},
        )
    )

    assert result.status == "updated"
    assert catalog.lookup(
        ResourceLookupRequest(resource="user")
    ).recommended_id == 9
    assert catalog.list_rules(operation.operation_key)[0].has_resource is True


def test_builder_selects_configured_fast_model(tmp_path: Path) -> None:
    from restscope.agent.api_behavior_monitor import build_api_behavior_monitor_agent
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

    agent = build_api_behavior_monitor_agent(
        RESTScopeConfig.from_environment(env_file),
        llm_client=client,
    )

    assert agent.client is client
    assert agent.resource_identifier_tracker.model.role == "api_behavior_monitor"
    assert agent.resource_identifier_tracker.model.model == "fast-model"


def test_resource_lookup_tool_returns_complete_structured_result(
    tmp_path: Path,
) -> None:
    from restscope.agent.api_behavior_monitor import (
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

    tracker, catalog = _agent(tmp_path, StubLLMClient())
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
    spec = register_resource_lookup_tool(registry, tracker)
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
        role="operation_smoke_plan_solve",
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


def test_first_observation_requests_bounded_alias_window(
    tmp_path: Path,
) -> None:
    client = StubLLMClient(_selection("I1"))
    tracker, catalog = _agent(tmp_path, client)
    original = catalog.list_resources
    calls: list[dict[str, int | None]] = []

    def capture(*, limit=None, aliases_per_resource=None):
        calls.append(
            {
                "limit": limit,
                "aliases_per_resource": aliases_per_resource,
            }
        )
        return original(
            limit=limit,
            aliases_per_resource=aliases_per_resource,
        )

    catalog.list_resources = capture

    assert tracker.observe(
        _observation(
            operation_key="POST /commits",
            path="/commits",
            body={"sha": "abc123"},
        )
    ).status == "updated"
    assert calls == [{"limit": 101, "aliases_per_resource": 21}]


def test_existing_resource_context_limits_fail_closed() -> None:
    import pytest

    from restscope.agent.api_behavior_monitor.resource_identifier import (
        _EvidenceLimitExceeded,
        _resource_prompt_context,
    )
    from restscope.agent.api_behavior_monitor.resource_schemas import (
        ResourceNameSummary,
    )

    with pytest.raises(_EvidenceLimitExceeded):
        _resource_prompt_context(
            [
                ResourceNameSummary(
                    resource_id=f"resource_{index}",
                    canonical_name=f"resource-{index}",
                    aliases=[f"alias-{index}"],
                )
                for index in range(101)
            ]
        )

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
