from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from restscope.llm import LLMResponse, ToolCall


def _openapi_document(*, paths: dict | None = None) -> dict:
    return {
        "openapi": "3.0.3",
        "info": {"title": "Retrieval", "version": "1.0.0"},
        "paths": paths
        or {
            "/users": {
                "post": {
                    "operationId": "createUser",
                    "responses": {
                        "201": {
                            "description": "created",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "userId": {"type": "string", "description": "Created user identifier"},
                                            "name": {"type": "string"},
                                        },
                                    }
                                }
                            },
                            "headers": {"X-User-Id": {"schema": {"type": "string"}}},
                        }
                    },
                }
            },
            "/users/{userId}": {
                "get": {
                    "operationId": "getUser",
                    "parameters": [
                        {"name": "userId", "in": "path", "required": True, "schema": {"type": "string"}}
                    ],
                    "responses": {"200": {"description": "ok"}},
                }
            },
            "/sessions": {
                "post": {
                    "operationId": "createSession",
                    "responses": {
                        "201": {
                            "description": "created",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {"sessionId": {"type": "string"}},
                                    }
                                }
                            },
                        }
                    },
                }
            },
        },
    }


def _ir(document: dict | str | None = None):
    from restscope.openapi_parser import OpenAPIParser

    return OpenAPIParser.parse(document or _openapi_document())


def _context(*, document: dict | None = None):
    from restscope.capabilities import ToolContext

    return ToolContext(
        ir=_ir(document),
        baseline_schema_source={"kind": "inline", "format": "json", "content": "{}"},
        base_url="https://api.example.test",
        headers={"Authorization": "Bearer runtime-secret"},
    )


def _retrieval_model():
    from restscope.llm import LLMModelConfig

    return LLMModelConfig(
        role="openapi_retrieval",
        provider="fake",
        model="thinking-test",
        max_tokens=2000,
        timeout_seconds=30,
        tool_choice="auto",
    )


def _retrieval_request(*, consumer_path: str = "/users/{userId}"):
    from restscope.agent.openapi_retrieval import OpenAPIRetrievalRequest, ParameterValueProducerQuery

    return OpenAPIRetrievalRequest(
        query=ParameterValueProducerQuery(
            objective="parameter_value_producer",
            consumer_method="GET",
            consumer_path=consumer_path,
            parameter_name="userId",
        )
    )


class _AdaptiveInvestigationClient:
    def __init__(self) -> None:
        self.requests = []

    def invoke(self, request):
        self.requests.append(request)
        call_number = len(self.requests)
        if call_number == 1:
            return LLMResponse(
                provider="fake",
                model=request.model,
                tool_calls=[
                    ToolCall(
                        id="inspect",
                        name="openapi.inspect",
                        arguments={},
                    )
                ],
            )
        if call_number == 2:
            return LLMResponse(
                provider="fake",
                model=request.model,
                tool_calls=[
                    ToolCall(
                        id="search_symbols",
                        name="openapi.search_symbols",
                        arguments={"query": "userId", "scopes": ["response_field"], "limit": 5},
                    )
                ],
            )
        tool_payload = json.loads(request.messages[-1].content)
        match = tool_payload["results"][0]
        return LLMResponse(
            provider="fake",
            model=request.model,
            parsed_json={
                "status": "found",
                "candidates": [
                    {
                        "operation": match["operation"],
                        "confidence": "high",
                        "value_locations": [match["location"]],
                        "rationale": "The successful response defines userId.",
                        "evidence_refs": [match["evidence_id"]],
                    }
                ],
                "conflicts": [],
                "evidence_sufficient": True,
                "limitations": [],
                "warnings": [],
            },
        )


class _RepairingInvestigationClient:
    def __init__(self) -> None:
        self.requests = []
        self.evidence_id = ""

    def invoke(self, request):
        self.requests.append(request)
        if len(self.requests) == 1:
            return LLMResponse(
                provider="fake",
                model=request.model,
                tool_calls=[
                    ToolCall(
                        id="search",
                        name="openapi.search_symbols",
                        arguments={"query": "userId", "scopes": ["response_field"], "limit": 5},
                    )
                ],
            )
        if len(self.requests) == 2:
            self.evidence_id = json.loads(request.messages[-1].content)["results"][0]["evidence_id"]
            return LLMResponse(
                provider="fake",
                model=request.model,
                parsed_json={
                    "status": "found",
                    "candidates": [
                        {
                            "operation": {
                                "method": "POST",
                                "path": "/sessions",
                                "operation_id": "createSession",
                            },
                            "confidence": "high",
                            "value_locations": ["POST /sessions response:201:sessionId"],
                            "rationale": "The evidence belongs to a different valid operation.",
                            "evidence_refs": [self.evidence_id],
                        }
                    ],
                    "conflicts": [],
                    "evidence_sufficient": True,
                    "limitations": [],
                    "warnings": [],
                },
            )
        return LLMResponse(
            provider="fake",
            model=request.model,
            parsed_json={
                "status": "found",
                "candidates": [
                    {
                        "operation": {"method": "POST", "path": "/users", "operation_id": "createUser"},
                        "confidence": "high",
                        "value_locations": ["POST /users response:201:application/json:userId"],
                        "rationale": "Corrected to the operation supported by evidence.",
                        "evidence_refs": [self.evidence_id],
                    }
                ],
                "conflicts": [],
                "evidence_sufficient": True,
                "limitations": [],
                "warnings": [],
            },
        )


class _BudgetInvestigationClient:
    def __init__(self) -> None:
        self.requests = []

    def invoke(self, request):
        self.requests.append(request)
        if request.tools:
            return LLMResponse(
                provider="fake",
                model=request.model,
                tool_calls=[ToolCall(id=f"inspect_{len(self.requests)}", name="openapi.inspect", arguments={})],
            )
        return LLMResponse(
            provider="fake",
            model=request.model,
            parsed_json={
                "status": "insufficient_evidence",
                "candidates": [],
                "conflicts": [],
                "evidence_sufficient": False,
                "limitations": ["Investigation tool-call budget exhausted."],
                "warnings": [],
            },
        )


class _InvalidConclusionClient:
    def __init__(self) -> None:
        self.requests = []

    def invoke(self, request):
        self.requests.append(request)
        return LLMResponse(
            provider="fake",
            model=request.model,
            parsed_json={"status": "found", "candidates": []},
        )


class _OneToolThenSummaryClient:
    def __init__(self) -> None:
        self.requests = []

    def invoke(self, request):
        self.requests.append(request)
        if request.tools:
            return LLMResponse(
                provider="fake",
                model=request.model,
                tool_calls=[ToolCall(id="inspect", name="openapi.inspect", arguments={})],
            )
        limitation = request.messages[-1].content.split(" Do not call more tools.", 1)[0]
        return LLMResponse(
            provider="fake",
            model=request.model,
            parsed_json={
                "status": "insufficient_evidence",
                "candidates": [],
                "conflicts": [],
                "evidence_sufficient": False,
                "limitations": [limitation],
                "warnings": [],
            },
        )


class _ConflictInvestigationClient:
    def __init__(self) -> None:
        self.requests = []
        self.field_match = {}
        self.operation_evidence_id = ""

    def invoke(self, request):
        self.requests.append(request)
        if len(self.requests) == 1:
            return LLMResponse(
                provider="fake",
                model=request.model,
                tool_calls=[
                    ToolCall(
                        id="find_field",
                        name="openapi.search_symbols",
                        arguments={"query": "userId", "scopes": ["response_field"], "limit": 2},
                    )
                ],
            )
        if len(self.requests) == 2:
            self.field_match = json.loads(request.messages[-1].content)["results"][0]
            operation = self.field_match["operation"]
            return LLMResponse(
                provider="fake",
                model=request.model,
                tool_calls=[
                    ToolCall(
                        id="read_producer",
                        name="openapi.read_operation",
                        arguments={
                            "operation_key": f"{operation['method']} {operation['path']}",
                            "sections": ["responses"],
                        },
                    )
                ],
            )
        if len(self.requests) == 3:
            self.operation_evidence_id = json.loads(request.messages[-1].content)["evidence_id"]
            return LLMResponse(
                provider="fake",
                model=request.model,
                tool_calls=[
                    ToolCall(
                        id="find_header",
                        name="openapi.search_symbols",
                        arguments={
                            "query": "X-User-Id",
                            "scopes": ["response_header"],
                            "limit": 2,
                        },
                    )
                ],
            )
        header_match = json.loads(request.messages[-1].content)["results"][0]
        return LLMResponse(
            provider="fake",
            model=request.model,
            parsed_json={
                "status": "insufficient_evidence",
                "candidates": [
                    {
                        "operation": self.field_match["operation"],
                        "confidence": "low",
                        "value_locations": [
                            self.field_match["location"],
                            header_match["location"],
                        ],
                        "rationale": "The operation exposes two plausible locations, but neither is linked to the consumer.",
                        "evidence_refs": [
                            self.field_match["evidence_id"],
                            self.operation_evidence_id,
                            header_match["evidence_id"],
                        ],
                    }
                ],
                "conflicts": [
                    {
                        "description": "The body field and response header are both plausible value locations.",
                        "evidence_refs": [
                            self.field_match["evidence_id"],
                            header_match["evidence_id"],
                        ],
                    }
                ],
                "evidence_sufficient": False,
                "limitations": ["No explicit OpenAPI Link connects either response location to the consumer."],
                "warnings": [],
            },
        )


def test_openapi_retrieval_request_contract_normalizes_consumer_method() -> None:
    from restscope.agent.openapi_retrieval import OpenAPIRetrievalRequest, ParameterValueProducerQuery

    request = OpenAPIRetrievalRequest(
        query=ParameterValueProducerQuery(
            objective="parameter_value_producer",
            consumer_method="get",
            consumer_path="/users/{userId}",
            parameter_name="userId",
        ),
    )

    assert request.query.consumer_method == "GET"
    assert request.query.limit == 10
    assert list(OpenAPIRetrievalRequest.model_fields) == ["query"]

    with pytest.raises(ValidationError):
        OpenAPIRetrievalRequest.model_validate(
            {"file_path": "api.yaml", "query": request.query.model_dump(mode="json")}
        )


def test_openapi_retrieval_query_rejects_non_path_consumer() -> None:
    from restscope.agent.openapi_retrieval import ParameterValueProducerQuery

    with pytest.raises(ValidationError):
        ParameterValueProducerQuery(
            objective="parameter_value_producer",
            consumer_method="GET",
            consumer_path="https://example.test/users/1",
            parameter_name="userId",
        )


def test_openapi_retrieval_uses_the_thinking_model_role() -> None:
    from restscope.llm import LLMModelConfig, ModelSelector

    selector = ModelSelector(
        thinking=LLMModelConfig(role="thinking", provider="fake", model="thinking-model"),
        fast=LLMModelConfig(role="fast", provider="fake", model="fast-model"),
    )

    selected = selector.select("openapi_retrieval")

    assert selected.role == "openapi_retrieval"
    assert selected.model == "thinking-model"


def test_workspace_uses_supplied_ir_without_reparsing(monkeypatch) -> None:
    from restscope.agent.openapi_retrieval.investigation import OpenAPIInvestigationWorkspace
    from restscope.openapi_parser import OpenAPIParser

    ir = _ir()
    monkeypatch.setattr(
        OpenAPIParser,
        "parse",
        staticmethod(lambda _source: (_ for _ in ()).throw(AssertionError("unexpected reparse"))),
    )

    workspace = OpenAPIInvestigationWorkspace(ir=ir)

    assert workspace.ir is ir
    assert workspace.find_operation(method="GET", path="/users/42").operation_id == "getUser"


def test_workspace_finds_target_parameter_in_multipart_request_body() -> None:
    from restscope.agent.openapi_retrieval.investigation import OpenAPIInvestigationTools, OpenAPIInvestigationWorkspace

    document = _openapi_document()
    document["paths"]["/imports"] = {
        "post": {
            "operationId": "importUser",
            "requestBody": {
                "required": True,
                "content": {
                    "multipart/form-data": {
                        "schema": {
                            "type": "object",
                            "required": ["userId"],
                            "properties": {
                                "userId": {"type": "string"},
                                "avatar": {"type": "string", "format": "binary"},
                            },
                        }
                    }
                },
            },
            "responses": {"202": {"description": "accepted"}},
        }
    }
    workspace = OpenAPIInvestigationWorkspace(ir=_ir(document))
    operation = workspace.find_operation(method="POST", path="/imports")

    target = workspace.find_target_parameter(operation, "userId")
    symbols = OpenAPIInvestigationTools(workspace).execute(
        "openapi.search_symbols",
        {"query": "userId", "scopes": ["parameter"], "limit": 10},
    )

    assert target.matches[0].location == "body"
    assert target.matches[0].field_path == "userId"
    assert target.matches[0].required is True
    assert any(item["operation"]["operation_id"] == "importUser" for item in symbols["results"])


def test_workspace_finds_target_parameter_in_composed_request_body() -> None:
    from restscope.agent.openapi_retrieval.investigation import OpenAPIInvestigationWorkspace

    document = _openapi_document()
    document["paths"]["/imports"] = {
        "post": {
            "operationId": "importUser",
            "requestBody": {
                "content": {
                    "application/json": {
                        "schema": {
                            "allOf": [
                                {
                                    "type": "object",
                                    "required": ["userId"],
                                    "properties": {"userId": {"type": "string"}},
                                },
                                {
                                    "type": "object",
                                    "properties": {"source": {"type": "string"}},
                                },
                            ]
                        }
                    }
                }
            },
            "responses": {"202": {"description": "accepted"}},
        }
    }
    workspace = OpenAPIInvestigationWorkspace(ir=_ir(document))
    operation = workspace.find_operation(method="POST", path="/imports")

    target = workspace.find_target_parameter(operation, "userId")

    assert target.matches[0].field_path == "userId"
    assert target.matches[0].required is True


def test_workspace_matches_template_and_concrete_consumer_paths() -> None:
    from restscope.agent.openapi_retrieval.investigation import OpenAPIInvestigationWorkspace

    workspace = OpenAPIInvestigationWorkspace(ir=_ir())

    exact = workspace.find_operation(method="GET", path="/users/{userId}")
    concrete = workspace.find_operation(method="GET", path="/users/123")

    assert exact.operation_id == "getUser"
    assert concrete.operation_id == "getUser"
    assert workspace.find_target_parameter(concrete, "userId").matches[0].location == "path"


def test_workspace_rejects_ambiguous_concrete_path() -> None:
    from restscope.agent.openapi_retrieval.investigation import OpenAPIRetrievalQueryError, OpenAPIInvestigationWorkspace

    ir = _ir(
        _openapi_document(
            paths={
                "/things/{id}": {"get": {"operationId": "byId", "responses": {"200": {"description": "ok"}}}},
                "/things/{name}": {
                    "get": {"operationId": "byName", "responses": {"200": {"description": "ok"}}}
                },
            }
        )
    )
    workspace = OpenAPIInvestigationWorkspace(ir=ir)

    with pytest.raises(OpenAPIRetrievalQueryError) as exc_info:
        workspace.find_operation(method="GET", path="/things/value")

    assert exc_info.value.code == "ambiguous_consumer_operation"


def test_internal_tools_search_symbols_and_expand_evidence() -> None:
    from restscope.agent.openapi_retrieval.investigation import OpenAPIInvestigationTools, OpenAPIInvestigationWorkspace

    tools = OpenAPIInvestigationTools(OpenAPIInvestigationWorkspace(ir=_ir()))

    search = tools.execute(
        "openapi.search_symbols",
        {"query": "userId", "scopes": ["response_field"], "limit": 5},
    )
    evidence_id = search["results"][0]["evidence_id"]
    expanded = tools.execute("openapi.read_evidence", {"evidence_ids": [evidence_id]})

    assert search["results"][0]["operation"]["operation_id"] == "createUser"
    assert search["results"][0]["location"].endswith("userId")
    assert expanded["evidence"][0]["id"] == evidence_id
    assert {spec.name for spec in tools.specs()} == {
        "openapi.inspect",
        "openapi.find_operation",
        "openapi.search_symbols",
        "openapi.read_operation",
        "openapi.read_evidence",
    }
    assert not hasattr(tools, "_symbols")


def test_symbol_search_scans_ir_each_time_without_a_cached_index() -> None:
    from restscope.agent.openapi_retrieval.investigation import OpenAPIInvestigationTools, OpenAPIInvestigationWorkspace

    ir = _ir()
    tools = OpenAPIInvestigationTools(OpenAPIInvestigationWorkspace(ir=ir))
    assert tools.execute("openapi.search_symbols", {"query": "lateField"})["results"] == []

    producer = next(operation for operation in ir.operations.values() if operation.operation_id == "createUser")
    schema = producer.responses.by_status["201"].contents["application/json"].schema
    assert schema is not None
    schema.properties["lateField"] = schema.properties["name"]

    results = tools.execute(
        "openapi.search_symbols",
        {"query": "lateField", "scopes": ["response_field"]},
    )["results"]
    assert results[0]["operation"]["operation_id"] == "createUser"


def test_symbol_search_finds_nested_response_field_through_resolved_ref() -> None:
    from restscope.agent.openapi_retrieval.investigation import OpenAPIInvestigationTools, OpenAPIInvestigationWorkspace

    document = _openapi_document()
    document["components"] = {
        "schemas": {
            "CreatedUser": {
                "type": "object",
                "properties": {"userId": {"type": "string"}},
            }
        }
    }
    document["paths"]["/users"]["post"]["responses"]["201"]["content"]["application/json"]["schema"] = {
        "type": "object",
        "properties": {"data": {"$ref": "#/components/schemas/CreatedUser"}},
    }
    tools = OpenAPIInvestigationTools(OpenAPIInvestigationWorkspace(ir=_ir(document)))

    results = tools.execute(
        "openapi.search_symbols",
        {"query": "data.userId", "scopes": ["response_field"]},
    )["results"]

    assert results[0]["operation"]["operation_id"] == "createUser"
    assert results[0]["location"].endswith("data.userId")


def test_internal_openapi_inspect_returns_parser_diagnostics() -> None:
    from restscope.agent.openapi_retrieval.investigation import OpenAPIInvestigationTools, OpenAPIInvestigationWorkspace

    tools = OpenAPIInvestigationTools(OpenAPIInvestigationWorkspace(ir=_ir()))

    result = tools.execute("openapi.inspect", {})

    assert result["format"] == "openapi3.0"
    assert result["operation_count"] == 3
    assert result["diagnostics"] == {
        "spec_errors": 0,
        "spec_warnings": 0,
        "path_errors": 0,
        "operation_errors": 0,
    }


def test_agent_changes_search_strategy_and_stops_when_evidence_is_sufficient() -> None:
    from restscope.agent.openapi_retrieval import OpenAPIRetrievalAgent

    client = _AdaptiveInvestigationClient()
    result = OpenAPIRetrievalAgent(client=client, model=_retrieval_model()).retrieve(
        _retrieval_request(consumer_path="/users/42"),
        ir=_ir(),
    )

    assert result.status == "found"
    assert result.candidates[0].operation.operation_id == "createUser"
    assert [action.tool for action in result.investigation_summary.actions] == [
        "openapi.inspect",
        "openapi.search_symbols",
    ]
    assert result.investigation_summary.evidence_sufficient is True
    assert len(client.requests) == 3
    assert client.requests[0].tool_choice == "auto"
    assert client.requests[1].messages[-2].tool_calls[0].id == "inspect"
    output_schema = client.requests[0].json_schema
    assert set(output_schema["required"]) == set(output_schema["properties"])
    for definition in output_schema["$defs"].values():
        if "properties" in definition:
            assert set(definition["required"]) == set(definition["properties"])
            assert definition["additionalProperties"] is False
    assert "default" not in output_schema["$defs"]["OperationReference"]["properties"]["operation_id"]
    assert client.requests[0].metadata["disable_retry"] is True


def test_agent_reads_symbol_context_and_preserves_conflicting_partial_evidence() -> None:
    from restscope.agent.openapi_retrieval import OpenAPIRetrievalAgent

    client = _ConflictInvestigationClient()
    result = OpenAPIRetrievalAgent(client=client, model=_retrieval_model()).retrieve(
        _retrieval_request(),
        ir=_ir(),
    )

    assert result.status == "insufficient_evidence"
    assert result.candidates[0].confidence == "low"
    assert len(result.conflicts) == 1
    assert len(result.conflicts[0].evidence_refs) == 2
    assert [action.tool for action in result.investigation_summary.actions] == [
        "openapi.search_symbols",
        "openapi.read_operation",
        "openapi.search_symbols",
    ]
    assert result.investigation_summary.evidence_sufficient is False


def test_agent_repairs_cross_operation_evidence_once() -> None:
    from restscope.agent.openapi_retrieval import OpenAPIRetrievalAgent

    client = _RepairingInvestigationClient()
    result = OpenAPIRetrievalAgent(client=client, model=_retrieval_model()).retrieve(
        _retrieval_request(),
        ir=_ir(),
    )

    assert result.candidates[0].operation.operation_id == "createUser"
    assert len(client.requests) == 3
    assert client.requests[-1].tools == []
    assert "does not belong" in client.requests[-1].messages[-1].content


def test_semantic_validation_rejects_fictional_operation_and_unbound_evidence() -> None:
    from restscope.agent.openapi_retrieval.agent import _semantic_errors
    from restscope.agent.openapi_retrieval.investigation import OpenAPIInvestigationWorkspace
    from restscope.agent.openapi_retrieval.schemas import OpenAPIRetrievalDraft, RetrievalEvidence

    workspace = OpenAPIInvestigationWorkspace(ir=_ir())
    consumer = workspace.find_operation(method="GET", path="/users/{userId}")
    evidence = RetrievalEvidence(
        id="evidence:unbound",
        kind="symbol",
        location="unbound-location",
        summary="No operation identity is attached.",
    )
    draft = OpenAPIRetrievalDraft.model_validate(
        {
            "status": "found",
            "candidates": [
                {
                    "operation": {"method": "POST", "path": "/invented", "operation_id": "invented"},
                    "confidence": "low",
                    "value_locations": [evidence.location],
                    "rationale": "Unsupported candidate",
                    "evidence_refs": [evidence.id],
                }
            ],
            "conflicts": [],
            "evidence_sufficient": True,
            "limitations": [],
            "warnings": [],
        }
    )

    errors = _semantic_errors(
        draft=draft,
        workspace=workspace,
        evidence_by_id={evidence.id: evidence},
        consumer=consumer,
        limit=10,
    )

    assert any("does not exist" in error for error in errors)
    assert any("requires operation-bound evidence" in error for error in errors)


def test_agent_forces_a_tool_free_summary_at_the_call_budget() -> None:
    from restscope.agent.openapi_retrieval import OpenAPIRetrievalAgent

    client = _BudgetInvestigationClient()
    result = OpenAPIRetrievalAgent(client=client, model=_retrieval_model()).retrieve(
        _retrieval_request(),
        ir=_ir(),
    )

    assert result.status == "insufficient_evidence"
    assert result.investigation_summary.tool_calls == 20
    assert result.investigation_summary.limitations == ["Investigation tool-call budget exhausted."]
    assert client.requests[-1].tools == []


def test_agent_forces_summary_at_the_tool_result_byte_budget(monkeypatch) -> None:
    import restscope.agent.openapi_retrieval.agent as agent_module
    from restscope.agent.openapi_retrieval import OpenAPIRetrievalAgent

    monkeypatch.setattr(agent_module, "MAX_TOOL_RESULT_BYTES", 1)
    client = _OneToolThenSummaryClient()
    result = OpenAPIRetrievalAgent(client=client, model=_retrieval_model()).retrieve(
        _retrieval_request(),
        ir=_ir(),
    )

    assert result.investigation_summary.tool_result_bytes == 1
    assert result.investigation_summary.limitations == [
        "Investigation tool-result byte budget exhausted."
    ]
    assert client.requests[-1].tools == []


def test_agent_forces_summary_at_the_time_budget() -> None:
    from restscope.agent.openapi_retrieval import OpenAPIRetrievalAgent

    ticks = iter([0.0, 91.0, 91.0, 91.0])
    client = _OneToolThenSummaryClient()
    result = OpenAPIRetrievalAgent(
        client=client,
        model=_retrieval_model(),
        clock=lambda: next(ticks),
    ).retrieve(
        _retrieval_request(),
        ir=_ir(),
    )

    assert result.investigation_summary.elapsed_ms == 91000
    assert result.investigation_summary.tool_calls == 0
    assert result.investigation_summary.limitations == ["Investigation time budget exhausted."]
    assert client.requests[-1].tools == []
    assert client.requests[-1].timeout_seconds == 29


def test_agent_fails_after_one_invalid_output_repair() -> None:
    from restscope.agent.openapi_retrieval import (
        OpenAPIRetrievalAgent,
        OpenAPIRetrievalOutputError,
    )

    client = _InvalidConclusionClient()
    with pytest.raises(OpenAPIRetrievalOutputError) as exc_info:
        OpenAPIRetrievalAgent(client=client, model=_retrieval_model()).retrieve(
            _retrieval_request(),
            ir=_ir(),
        )

    assert exc_info.value.code == "openapi_retrieval_output_invalid"
    assert len(client.requests) == 2
    assert client.requests[-1].tools == []


def test_openapi_retrieval_tool_registers_and_executes_through_capability_runtime() -> None:
    from restscope.agent.openapi_retrieval import (
        OpenAPIRetrievalAgent,
        register_openapi_retrieval_tool,
    )
    from restscope.capabilities import ToolCallValidator, ToolExecutor, ToolPolicy, ToolRegistry

    registry = ToolRegistry()
    spec = register_openapi_retrieval_tool(
        registry=registry,
        agent=OpenAPIRetrievalAgent(client=_AdaptiveInvestigationClient(), model=_retrieval_model()),
    )
    request = _retrieval_request()
    executor = ToolExecutor(registry, ToolCallValidator(registry, ToolPolicy()))
    executor.bind_context(_context())
    result = executor.execute(
        tool_call=ToolCall(id="retrieve", name=spec.name, arguments=request.model_dump(mode="json")),
        role="decision_maker",
        state={},
    )

    assert spec.name == "restscope.openapi.retrieve"
    assert spec.kind == "local_function"
    assert spec.read_only is True
    assert spec.risk_level == "medium"
    assert spec.requires_approval is False
    assert result.status == "succeeded"
    assert result.structured["candidates"][0]["operation"]["operation_id"] == "createUser"
    assert "createUser" in result.content
    assert "file_path" not in spec.input_schema.get("properties", {})
    assert "runtime-secret" not in result.model_dump_json()


def test_openapi_retrieval_tool_preserves_stable_query_error_code() -> None:
    from restscope.agent.openapi_retrieval import OpenAPIRetrievalAgent, register_openapi_retrieval_tool
    from restscope.capabilities import ToolCallValidator, ToolExecutor, ToolPolicy, ToolRegistry

    registry = ToolRegistry()
    register_openapi_retrieval_tool(
        registry=registry,
        agent=OpenAPIRetrievalAgent(client=_AdaptiveInvestigationClient(), model=_retrieval_model()),
    )
    executor = ToolExecutor(registry, ToolCallValidator(registry, ToolPolicy()))
    executor.bind_context(_context())
    result = executor.execute(
        tool_call=ToolCall(
            id="missing",
            name="restscope.openapi.retrieve",
            arguments={
                "query": {
                    "objective": "parameter_value_producer",
                    "consumer_method": "GET",
                    "consumer_path": "/missing/{userId}",
                    "parameter_name": "userId",
                },
            },
        ),
        role="decision_maker",
        state={},
    )

    assert result.status == "failed"
    assert result.error["code"] == "consumer_operation_not_found"


def test_build_openapi_retrieval_agent_uses_configured_thinking_model(tmp_path) -> None:
    from restscope.agent.openapi_retrieval import build_openapi_retrieval_agent
    from restscope.restscope_config import RESTScopeConfig

    env_file = tmp_path / ".env"
    env_file.write_text("THINK_PROVIDER=fake\nTHINK_MODEL=retrieval-model\n", encoding="utf-8")

    agent = build_openapi_retrieval_agent(
        RESTScopeConfig.from_environment(env_file),
        llm_client=_AdaptiveInvestigationClient(),
    )

    assert agent.model.role == "openapi_retrieval"
    assert agent.model.model == "retrieval-model"


def test_openapi_retrieval_public_facades_export_the_same_agent() -> None:
    from restscope.agent import OpenAPIRetrievalAgent as FacadeAgent
    from restscope.agent.openapi_retrieval import OpenAPIRetrievalAgent as PackageAgent

    assert FacadeAgent is PackageAgent


def test_live_retrieval_config_can_select_complete_fast_model_slot(tmp_path) -> None:
    from tests import test_openapi_retrieval_agent_live as live_module

    from restscope.restscope_config import RESTScopeConfig

    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "THINK_PROVIDER=deepseek",
                "THINK_MODEL=deepseek-v4-pro",
                "THINK_API_KEY=thinking-key",
                "THINK_REASONING_MODE=enabled",
                "THINK_REASONING_EFFORT=high",
                "FAST_PROVIDER=deepseek",
                "FAST_MODEL=deepseek-v4-flash",
                "FAST_API_KEY=fast-key",
                "FAST_REASONING_MODE=disabled",
                "FAST_REASONING_EFFORT=",
            ]
        ),
        encoding="utf-8",
    )
    config = RESTScopeConfig.from_environment(env_file)
    selector = getattr(live_module, "_config_for_live_model_slot", None)

    assert selector is not None
    selected = selector(config, "fast")

    assert selected.llm.thinking == config.llm.fast
    assert selected.llm.fast == config.llm.fast
    assert config.llm.thinking.model == "deepseek-v4-pro"


def test_live_retrieval_config_rejects_unknown_model_slot(tmp_path) -> None:
    from tests.test_openapi_retrieval_agent_live import _config_for_live_model_slot

    from restscope.restscope_config import RESTScopeConfig

    config = RESTScopeConfig.from_environment(tmp_path / ".env")

    with pytest.raises(
        ValueError,
        match="OPENAPI_RETRIEVAL_LIVE_MODEL_SLOT must be 'thinking' or 'fast'",
    ):
        _config_for_live_model_slot(config, "turbo")


def test_live_retrieval_closes_tracing_runtime_when_agent_fails(
    tmp_path,
    monkeypatch,
) -> None:
    from tests import test_openapi_retrieval_agent_live as live_module

    from restscope.restscope_config import RESTScopeConfig

    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "THINK_PROVIDER=deepseek",
                "THINK_MODEL=deepseek-v4-pro",
                "THINK_API_KEY=thinking-key",
                "FAST_PROVIDER=deepseek",
                "FAST_MODEL=deepseek-v4-flash",
                "FAST_API_KEY=fast-key",
            ]
        ),
        encoding="utf-8",
    )
    base_config = RESTScopeConfig.from_environment(env_file)
    config = live_module._config_for_live_model_slot(base_config, "fast")
    runtime_calls: list[tuple[object, tuple[str, ...]]] = []

    class Runtime:
        closed = False

        def close(self) -> None:
            self.closed = True

    class Agent:
        def retrieve(self, request, *, ir):
            del request, ir
            raise RuntimeError("agent failed")

    runtime = Runtime()

    def build_runtime(tracing_config, *, secret_values):
        runtime_calls.append((tracing_config, tuple(secret_values)))
        return runtime

    def build_agent(agent_config, *, tracing_runtime):
        assert agent_config is config
        assert tracing_runtime is runtime
        return Agent()

    monkeypatch.setattr(live_module, "build_tracing_runtime", build_runtime)
    monkeypatch.setattr(live_module, "build_openapi_retrieval_agent", build_agent)
    runner = getattr(live_module, "_retrieve_with_tracing", None)

    assert runner is not None
    with pytest.raises(RuntimeError, match="agent failed"):
        runner(
            base_config=base_config,
            config=config,
            request=object(),
            ir=object(),
        )

    assert runtime.closed is True
    assert runtime_calls == [
        (
            config.tracing,
            ("thinking-key", "fast-key"),
        )
    ]
