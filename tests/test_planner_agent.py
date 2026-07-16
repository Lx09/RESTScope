from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest


def _config(tmp_path: Path):
    from restscope.db import Base, create_engine_from_config
    from restscope.restscope_config import DBConfig, LLMConfig, ModelConfig, PathsConfig, RESTScopeConfig

    tmp_path.mkdir(parents=True, exist_ok=True)
    config = RESTScopeConfig.from_environment()
    thinking = ModelConfig(provider="fake", model="thinking-model", max_tokens=4096)
    config = replace(
        config,
        paths=PathsConfig(data_dir=tmp_path / "data"),
        db=DBConfig(url=f"sqlite:///{tmp_path / 'planner.sqlite'}"),
        llm=LLMConfig(thinking=thinking, fast=thinking),
    )
    Base.metadata.create_all(create_engine_from_config(config.db))
    return config


def _spec() -> dict:
    return {
        "openapi": "3.0.3",
        "info": {"title": "Orders", "version": "1.0.0"},
        "paths": {
            "/orders": {
                "post": {
                    "operationId": "createOrder",
                    "responses": {
                        "201": {
                            "description": "created",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {"id": {"type": "string"}},
                                    }
                                }
                            },
                        }
                    },
                }
            },
            "/orders/{id}": {
                "get": {
                    "operationId": "getOrder",
                    "parameters": [
                        {"name": "id", "in": "path", "required": True, "schema": {"type": "string"}}
                    ],
                    "responses": {"200": {"description": "found"}},
                }
            },
        },
    }


def _bootstrap(tmp_path: Path):
    from restscope.catalog import OpenAPIInitializationRequest, initialize_openapi_catalog
    from restscope.db import UnitOfWork, create_engine_from_config
    from restscope.db.session import make_session_factory

    config = _config(tmp_path)
    initialized = initialize_openapi_catalog(
        config,
        OpenAPIInitializationRequest(source=_spec(), name="Orders"),
    )
    factory = make_session_factory(create_engine_from_config(config.db))
    with UnitOfWork(factory) as uow:
        operations = uow.operations.list_by_schema(initialized.schema_id)
        uow.tasks.add(
            id="task_1",
            schema_id=initialized.schema_id,
            state="planning",
            goal_json={"goal": "find contract and workflow defects"},
            budget_json={"requirements": 10},
            selected_operation_ids=[operation.id for operation in operations],
        )
        uow.commit()
    by_name = {operation.operation_id: operation.id for operation in operations}
    return config, initialized.schema_id, by_name


def _draft(create_id: str, get_id: str) -> dict:
    return {
        "requirements": [
            {
                "kind": "single_operation",
                "title": "Create order validation",
                "priority": "high",
                "objective": "Validate order creation contract",
                "target": {"operation_id": create_id},
                "test_focus": ["required fields", "response schema"],
                "expected_behaviors": ["Valid input returns a documented success response"],
                "rationale": "Creation is the entry point for order workflows.",
                "evidence_refs": [f"operation:{create_id}"],
            },
            {
                "kind": "workflow",
                "title": "Create then retrieve order",
                "priority": "medium",
                "objective": "Validate produced identifiers can be consumed",
                "target": {
                    "steps": [
                        {"order": 1, "operation_id": create_id},
                        {
                            "order": 2,
                            "operation_id": get_id,
                            "data_dependency": "Use the created order id",
                        },
                    ]
                },
                "test_focus": ["identifier propagation"],
                "expected_behaviors": ["The created order can be retrieved by id"],
                "rationale": "The OpenAPI flow graph links these operations.",
                "evidence_refs": [f"operation:{create_id}", f"operation:{get_id}"],
            },
        ]
    }


class SequencedLLMClient:
    def __init__(self, payloads: list[dict]) -> None:
        self.payloads = list(payloads)
        self.requests = []

    def invoke(self, request):
        from restscope.llm import LLMResponse

        self.requests.append(request)
        payload = self.payloads.pop(0)
        return LLMResponse(provider="fake", model=request.model, parsed_json=payload)


def test_planner_public_imports() -> None:
    from restscope import PlannerRequest, RESTScopeConfig, build_planner_agent

    assert PlannerRequest is not None
    assert RESTScopeConfig is not None
    assert build_planner_agent is not None


def test_planner_generates_mixed_database_only_plan_and_immutable_revision(tmp_path: Path, monkeypatch) -> None:
    from restscope.agent import PlannerRequest, build_planner_agent
    from restscope.db import UnitOfWork, create_engine_from_config
    from restscope.db.session import make_session_factory
    from restscope.openapi_parser import OpenAPIParser

    config, schema_id, operations = _bootstrap(tmp_path)
    payload = _draft(operations["createOrder"], operations["getOrder"])
    client = SequencedLLMClient([payload, payload])

    monkeypatch.setattr(OpenAPIParser, "parse", lambda source: (_ for _ in ()).throw(AssertionError("reloaded")))
    planner = build_planner_agent(config, schema_id, llm_client=client)
    first = planner.plan(PlannerRequest(task_id="task_1"))

    factory = make_session_factory(create_engine_from_config(config.db))
    with UnitOfWork(factory) as uow:
        uow.campaigns.add(
            id="camp_1",
            task_id="task_1",
            schema_id=schema_id,
            status="completed",
            campaign_type="contract_test",
            campaign_spec_json={"operation_id": operations["createOrder"]},
        )
        uow.observations.upsert_observed(
            id="obs_1",
            task_id="task_1",
            campaign_id="camp_1",
            schema_id=schema_id,
            operation_id=operations["createOrder"],
            observation_type="contract_violation",
            severity="high",
            dedupe_key="create-order-contract",
        )
        uow.commit()
    second = planner.plan(PlannerRequest(task_id="task_1"))

    assert first.plan.revision == 1
    assert second.plan.revision == 2
    assert second.plan.previous_plan_id == first.plan.plan_id
    assert {item.kind for item in first.plan.requirements} == {"single_operation", "workflow"}
    assert {item.requirement_id for item in first.plan.requirements}.isdisjoint(
        {item.requirement_id for item in second.plan.requirements}
    )
    assert all(request.tools == [] and request.tool_choice == "none" for request in client.requests)
    assert "schemathesis" not in " ".join(
        message.content for request in client.requests for message in request.messages
    ).lower()
    assert "obs_1" in " ".join(message.content for message in client.requests[1].messages)

    with UnitOfWork(factory) as uow:
        plans = [
            item for item in uow.artifacts.list_by_task("task_1")
            if item.artifact_type == "test_requirement_plan"
        ]
    assert len(plans) == 2
    assert plans[0].artifact_uri != plans[1].artifact_uri


def test_planner_repairs_invalid_operation_reference_once(tmp_path: Path) -> None:
    from restscope.agent import PlannerRequest, build_planner_agent

    config, schema_id, operations = _bootstrap(tmp_path)
    invalid = _draft("op_missing", operations["getOrder"])
    valid = _draft(operations["createOrder"], operations["getOrder"])
    client = SequencedLLMClient([invalid, valid])

    result = build_planner_agent(config, schema_id, llm_client=client).plan(
        PlannerRequest(task_id="task_1")
    )

    assert result.plan.revision == 1
    assert len(client.requests) == 2
    assert "op_missing" in client.requests[1].messages[-1].content


def test_planner_rejects_two_invalid_outputs_without_plan_artifact(tmp_path: Path) -> None:
    from restscope.agent import PlannerError, PlannerRequest, build_planner_agent
    from restscope.db import UnitOfWork, create_engine_from_config
    from restscope.db.session import make_session_factory

    config, schema_id, operations = _bootstrap(tmp_path)
    invalid = _draft("op_missing", operations["getOrder"])
    client = SequencedLLMClient([invalid, invalid])

    with pytest.raises(PlannerError) as exc_info:
        build_planner_agent(config, schema_id, llm_client=client).plan(
            PlannerRequest(task_id="task_1")
        )

    assert exc_info.value.code == "planner_output_invalid"
    factory = make_session_factory(create_engine_from_config(config.db))
    with UnitOfWork(factory) as uow:
        assert not any(
            item.artifact_type == "test_requirement_plan"
            for item in uow.artifacts.list_by_task("task_1")
        )


def test_planner_rejects_task_schema_mismatch(tmp_path: Path) -> None:
    from restscope.agent import PlannerError, PlannerRequest, build_planner_agent
    from restscope.db import UnitOfWork, create_engine_from_config
    from restscope.db.session import make_session_factory

    config, schema_id, operations = _bootstrap(tmp_path)
    factory = make_session_factory(create_engine_from_config(config.db))
    with UnitOfWork(factory) as uow:
        uow.schemas.add(
            id="schema_other",
            name="Other",
            spec_hash="other-hash",
            raw_spec_uri="memory://other",
        )
        uow.tasks.add(
            id="task_other",
            schema_id="schema_other",
            state="planning",
            goal_json={"goal": "other"},
            budget_json={},
        )
        uow.commit()

    client = SequencedLLMClient([_draft(operations["createOrder"], operations["getOrder"])])
    with pytest.raises(PlannerError) as exc_info:
        build_planner_agent(config, schema_id, llm_client=client).plan(
            PlannerRequest(task_id="task_other")
        )

    assert exc_info.value.code == "schema_mismatch"
    assert client.requests == []


def test_planner_reports_catalog_not_ready_and_task_not_found(tmp_path: Path) -> None:
    from restscope.agent import PlannerError, PlannerRequest, build_planner_agent
    from restscope.db import UnitOfWork, create_engine_from_config
    from restscope.db.session import make_session_factory

    config = _config(tmp_path)
    factory = make_session_factory(create_engine_from_config(config.db))
    with UnitOfWork(factory) as uow:
        uow.schemas.add(
            id="schema_legacy",
            name="Legacy",
            spec_hash="legacy-hash",
            raw_spec_uri="memory://legacy",
        )
        uow.commit()

    with pytest.raises(PlannerError) as not_ready:
        build_planner_agent(config, "schema_legacy")
    assert not_ready.value.code == "catalog_not_ready"

    config, schema_id, _ = _bootstrap(tmp_path / "ready")
    client = SequencedLLMClient([])
    with pytest.raises(PlannerError) as missing_task:
        build_planner_agent(config, schema_id, llm_client=client).plan(
            PlannerRequest(task_id="task_missing")
        )
    assert missing_task.value.code == "task_not_found"
    assert client.requests == []


def test_planner_factory_uses_configured_fake_thinking_model(tmp_path: Path) -> None:
    from restscope.agent import PlannerRequest, build_planner_agent

    config, schema_id, _ = _bootstrap(tmp_path)
    result = build_planner_agent(config, schema_id).plan(PlannerRequest(task_id="task_1"))

    assert result.plan.revision == 1
    assert result.plan.requirements[0].kind == "single_operation"
