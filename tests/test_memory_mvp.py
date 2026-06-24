from __future__ import annotations

from decimal import Decimal
from pathlib import Path


def _make_session_factory(tmp_path: Path):
    from restscope.db import Base, create_engine_from_url
    from restscope.db.session import make_session_factory

    engine = create_engine_from_url(f"sqlite:///{tmp_path / 'memory.sqlite'}")
    Base.metadata.create_all(engine)
    return make_session_factory(engine)


def _seed_memory_db(session_factory) -> None:
    from restscope.db import UnitOfWork

    with UnitOfWork(session_factory) as uow:
        uow.schemas.add(
            id="schema_1",
            name="Petstore",
            spec_hash="hash-1",
            raw_spec_uri="file://petstore.json",
        )
        uow.operations.add(
            id="op_high",
            schema_id="schema_1",
            operation_id="createPet",
            method="POST",
            path="/pets",
            tags=["pets"],
            summary="Create pet",
            resource="pets",
            mutability="create",
            request_schema_refs=["PetCreate"],
            response_schema_refs=["Pet"],
            card_json={"method": "POST", "path": "/pets"},
            static_risk_score=Decimal("0.6"),
        )
        uow.operations.add(
            id="op_low",
            schema_id="schema_1",
            operation_id="listPets",
            method="GET",
            path="/pets",
            tags=["pets"],
            summary="List pets",
            resource="pets",
            mutability="read",
            card_json={"method": "GET", "path": "/pets"},
            static_risk_score=Decimal("0.1"),
        )
        uow.intelligence.add(
            operation_id="op_high",
            schema_id="schema_1",
            dynamic_risk_score=Decimal("0.9"),
            failure_density=Decimal("0.4"),
            flake_rate=Decimal("0.0"),
            recommended_checks=["response_schema", "negative_boundary"],
        )
        uow.intelligence.add(
            operation_id="op_low",
            schema_id="schema_1",
            dynamic_risk_score=Decimal("0.1"),
            failure_density=Decimal("0.0"),
            flake_rate=Decimal("0.0"),
        )
        uow.tasks.add(
            id="task_1",
            schema_id="schema_1",
            state="planning",
            goal_json={"goal": "find API bugs"},
            budget_json={"campaigns_remaining": 3},
            cycle_index=2,
            selected_operation_ids=["op_low"],
            current_hypotheses=["POST /pets may accept invalid age"],
            blockers_json=[{"kind": "approval", "message": "needs write approval"}],
        )
        uow.campaigns.add(
            id="camp_1",
            task_id="task_1",
            schema_id="schema_1",
            status="completed",
            campaign_type="risk_targeted_fuzzing",
            campaign_spec_json={"operations": ["op_high"]},
            summary_json={"covered_operation_count": 1, "observation_count": 1},
            artifact_bundle_uri="artifact://bundle-1",
        )
        uow.observations.upsert_observed(
            id="obs_1",
            task_id="task_1",
            campaign_id="camp_1",
            schema_id="schema_1",
            operation_id="op_high",
            observation_type="server_error",
            status="observed",
            severity="high",
            confidence=Decimal("0.9"),
            dedupe_key="POST /pets age=-1 500",
            request_summary_json={"body": {"age": -1}, "raw": "do-not-include"},
            response_summary_json={"status_code": 500, "raw": "do-not-include"},
            reproducer_artifact_id="artifact_repro",
            raw_artifact_id="artifact_raw",
        )
        uow.observations.upsert_observed(
            id="obs_ignored",
            task_id="task_1",
            campaign_id="camp_1",
            schema_id="schema_1",
            operation_id="op_low",
            observation_type="flake_suspect",
            status="ignored",
            severity="low",
            confidence=Decimal("0.2"),
            dedupe_key="GET /pets ignored",
        )
        uow.artifacts.add(
            id="artifact_repro",
            task_id="task_1",
            campaign_id="camp_1",
            observation_id="obs_1",
            artifact_type="reproducer",
            artifact_uri="file://reproducer.py",
            metadata_json={"note": "metadata only"},
        )
        uow.context_snapshots.add(
            id="ctx_1",
            task_id="task_1",
            schema_id="schema_1",
            role="planner",
            cycle_index=1,
            artifact_uri="file://context-1.json",
            source_refs_json={"operations": ["op_high"]},
            prompt_version="planner_v1",
            model_name="glm-4.5-air",
        )
        uow.events.append(
            id=None,
            task_id="task_1",
            campaign_id="camp_1",
            event_type="campaign_finished",
            actor="runner",
            payload_json={"campaign_id": "camp_1"},
        )
        uow.commit()


def test_memory_schema_defaults_and_source_refs() -> None:
    from restscope.memory import MemoryItem, MemoryPackage

    item = MemoryItem(
        id="mem_1",
        kind="operation",
        title="POST /pets",
        content="High risk operation",
        source_table="operations",
        source_id="op_high",
    )

    package = MemoryPackage.from_items(
        schema_id="schema_1",
        task_id="task_1",
        role="planner",
        items=[item],
    )

    assert item.importance == 0.5
    assert package.operation_memory == [item]
    assert package.source_refs == {"operations": ["op_high"]}


def test_planner_memory_retrieval_reads_all_mvp_memory_types(tmp_path: Path) -> None:
    from restscope.db import UnitOfWork
    from restscope.memory import MemoryService

    session_factory = _make_session_factory(tmp_path)
    _seed_memory_db(session_factory)

    with UnitOfWork(session_factory) as uow:
        before = {
            "tasks": len(uow.tasks.list()),
            "observations": len(uow.observations.list()),
            "events": len(uow.events.list()),
        }
        package = MemoryService.from_unit_of_work(uow).retrieve_for_planner(
            task_id="task_1",
            schema_id="schema_1",
            token_budget=4000,
        )
        after = {
            "tasks": len(uow.tasks.list()),
            "observations": len(uow.observations.list()),
            "events": len(uow.events.list()),
        }

    assert before == after
    assert package.working_memory[0].structured["state"] == "planning"
    assert "POST /pets may accept invalid age" in package.working_memory[0].structured["current_hypotheses"]
    assert {item.operation_id for item in package.operation_memory} == {"op_high", "op_low"}
    assert package.observation_memory[0].source_table == "test_observations"
    assert package.observation_memory[0].structured["reproducer_artifact_id"] == "artifact_repro"
    assert "do-not-include" not in package.observation_memory[0].content
    assert package.campaign_memory[0].campaign_id == "camp_1"
    assert any(item.source_table == "event_log" for item in package.episodic_memory)
    assert any(item.source_table == "context_snapshots" for item in package.episodic_memory)
    assert package.source_refs["operation_intelligence"] == ["op_high", "op_low"]
    assert package.source_refs["test_observations"] == ["obs_1"]


def test_role_specific_retrievals_focus_requested_inputs(tmp_path: Path) -> None:
    from restscope.db import UnitOfWork
    from restscope.memory import MemoryService

    session_factory = _make_session_factory(tmp_path)
    _seed_memory_db(session_factory)

    with UnitOfWork(session_factory) as uow:
        service = MemoryService.from_unit_of_work(uow)
        analyst = service.retrieve_for_result_analyst(
            task_id="task_1",
            schema_id="schema_1",
            campaign_id="camp_1",
            operation_ids=["op_high"],
            token_budget=4000,
        )
        decision = service.retrieve_for_decision_maker(
            task_id="task_1",
            schema_id="schema_1",
            token_budget=4000,
        )
        check = service.retrieve_for_check_designer(
            task_id="task_1",
            schema_id="schema_1",
            operation_ids=["op_high"],
            token_budget=4000,
        )

    assert analyst.role == "result_analyst"
    assert {item.operation_id for item in analyst.operation_memory} == {"op_high"}
    assert analyst.campaign_memory[0].campaign_id == "camp_1"
    assert decision.role == "decision_maker"
    assert decision.observation_memory[0].structured["status"] == "observed"
    assert check.role == "check_designer"
    assert check.constraint_memory == []
    assert check.testing_knowledge_memory == []


def test_ranker_and_compressor_prioritize_and_budget_items() -> None:
    from restscope.memory import MemoryCompressor, MemoryItem, MemoryQuery, MemoryRanker

    high = MemoryItem(
        id="high",
        kind="observation",
        title="Repeated server error",
        content="server_error " * 80,
        importance=0.95,
        confidence=0.9,
        recency_score=0.7,
        relevance_score=0.9,
        risk_score=0.8,
        source_table="test_observations",
        source_id="obs_1",
    )
    low = MemoryItem(
        id="low",
        kind="operation",
        title="Recent low risk",
        content="recent but low risk",
        importance=0.2,
        confidence=0.5,
        recency_score=1.0,
        relevance_score=0.2,
        risk_score=0.1,
        source_table="operations",
        source_id="op_low",
    )
    duplicate = high.model_copy(update={"id": "high_dup"})
    query = MemoryQuery(schema_id="schema_1", role="planner", token_budget=20)

    ranked = MemoryRanker().rank([low, high], query)
    compressed = MemoryCompressor().fit_budget([high, duplicate, low], token_budget=20)

    assert ranked[0].id == "high"
    assert [item.source_id for item in compressed].count("obs_1") == 1
    assert sum(item.estimated_tokens for item in compressed) <= 20
