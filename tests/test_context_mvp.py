from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path


def _make_session_factory(tmp_path: Path):
    from restscope.db import Base, create_engine_from_url
    from restscope.db.session import make_session_factory

    engine = create_engine_from_url(f"sqlite:///{tmp_path / 'context.sqlite'}")
    Base.metadata.create_all(engine)
    return make_session_factory(engine)


def _seed_context_db(session_factory) -> None:
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
        uow.intelligence.add(
            operation_id="op_high",
            schema_id="schema_1",
            dynamic_risk_score=Decimal("0.9"),
            failure_density=Decimal("0.4"),
            recommended_checks=["response_schema", "negative_boundary"],
        )
        uow.tasks.add(
            id="task_1",
            schema_id="schema_1",
            state="planning",
            goal_json={"goal": "find API bugs", "target": "live test env"},
            budget_json={"campaigns_remaining": 3, "max_examples": 100},
            cycle_index=2,
            selected_operation_ids=["op_high"],
            current_hypotheses=["POST /pets may accept invalid age"],
        )
        uow.campaigns.add(
            id="camp_1",
            task_id="task_1",
            schema_id="schema_1",
            status="completed",
            campaign_type="risk_targeted_fuzzing",
            campaign_spec_json={"operations": ["op_high"]},
            summary_json={
                "covered_operation_count": 1,
                "observation_count": 1,
                "raw": "raw campaign output should not appear",
            },
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
            request_summary_json={"body": {"age": -1}, "raw": "raw request should not appear"},
            response_summary_json={"status_code": 500, "raw": "raw response should not appear"},
            reproducer_artifact_id="artifact_repro",
            raw_artifact_id="artifact_raw",
        )
        uow.events.append(
            task_id="task_1",
            campaign_id="camp_1",
            event_type="campaign_finished",
            actor="runner",
            payload_json={"campaign_id": "camp_1"},
        )
        uow.commit()


def _make_builder(tmp_path: Path, session_factory):
    from restscope.context import ContextBuilder
    from restscope.context.context_snapshot_service import (
        ContextSnapshotService,
        LocalContextArtifactStore,
    )
    from restscope.db import UnitOfWork
    from restscope.memory import MemoryService

    uow = UnitOfWork(session_factory)
    uow.__enter__()
    memory_service = MemoryService.from_unit_of_work(uow)
    snapshot_service = ContextSnapshotService(
        artifact_store=LocalContextArtifactStore(tmp_path / "context_artifacts"),
        artifact_repo=uow.artifacts,
        context_snapshot_repo=uow.context_snapshots,
        event_log_repo=uow.events,
    )
    return ContextBuilder(memory_service=memory_service, snapshot_service=snapshot_service), uow


def test_planner_context_builds_messages_and_persists_snapshot(tmp_path: Path) -> None:
    from restscope.context import ContextBuildRequest

    session_factory = _make_session_factory(tmp_path)
    _seed_context_db(session_factory)
    builder, uow = _make_builder(tmp_path, session_factory)
    try:
        context = builder.build(
            ContextBuildRequest(
                task_id="task_1",
                schema_id="schema_1",
                role="planner",
                model_name="glm-4.5-air",
                token_budget=6000,
            )
        )
        uow.commit()
    finally:
        uow.__exit__(None, None, None)

    assert context.output_contract.name == "TestRequirementPlanDraft"
    assert [message.role for message in context.messages] == ["system", "user"]
    section_kinds = {section.kind for section in context.sections}
    assert {
        "role_instruction",
        "task_state",
        "test_goal",
        "budget",
        "operation_targets",
        "operation_risk_profile",
        "testing_evidence",
        "operation_relationships",
        "prior_requirement_plan",
        "output_contract",
    }.issubset(section_kinds)
    user_content = context.messages[1].content
    assert "schemathesis" not in user_content.lower()
    assert "campaign configuration" not in user_content.lower()
    assert context.artifact_uri is not None
    assert context.metadata["context_snapshot_id"]

    artifact_path = Path(context.artifact_uri.removeprefix("file://"))
    payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    assert payload["messages"] == [message.model_dump(mode="json") for message in context.messages]
    assert payload["source_refs"] == context.source_refs

    from restscope.db import UnitOfWork

    with UnitOfWork(session_factory) as check_uow:
        snapshots = check_uow.context_snapshots.list_by_task("task_1")
        artifacts = check_uow.artifacts.list_by_task("task_1")
        events = check_uow.events.list_by_task("task_1")

    assert len(snapshots) == 1
    assert snapshots[0].source_refs_json == context.source_refs
    assert any(artifact.artifact_type == "context_snapshot" for artifact in artifacts)
    assert any(event.event_type == "context_built" for event in events)


def test_result_analyst_and_decision_contexts_are_role_specific(tmp_path: Path) -> None:
    from restscope.context import ContextBuildRequest

    session_factory = _make_session_factory(tmp_path)
    _seed_context_db(session_factory)
    builder, uow = _make_builder(tmp_path, session_factory)
    try:
        analyst = builder.build(
            ContextBuildRequest(
                task_id="task_1",
                schema_id="schema_1",
                role="result_analyst",
                campaign_id="camp_1",
                operation_ids=["op_high"],
                token_budget=8000,
            )
        )
        decision = builder.build(
            ContextBuildRequest(
                task_id="task_1",
                schema_id="schema_1",
                role="decision_maker",
                token_budget=4000,
            )
        )
        uow.commit()
    finally:
        uow.__exit__(None, None, None)

    assert analyst.output_contract.name == "AnalysisResult"
    assert "Current campaign result" in analyst.messages[1].content
    assert "raw request should not appear" not in analyst.messages[1].content
    assert "raw response should not appear" not in analyst.messages[1].content
    assert decision.output_contract.name == "DecisionGateOutput"
    assert "Recent task events" in decision.messages[1].content
    assert "Required output" in decision.messages[1].content


def test_context_budget_keeps_required_sections_and_trims_optional(tmp_path: Path) -> None:
    from restscope.context import ContextBuildRequest

    session_factory = _make_session_factory(tmp_path)
    _seed_context_db(session_factory)
    builder, uow = _make_builder(tmp_path, session_factory)
    try:
        context = builder.build(
            ContextBuildRequest(
                task_id="task_1",
                schema_id="schema_1",
                role="planner",
                token_budget=30,
            )
        )
        uow.commit()
    finally:
        uow.__exit__(None, None, None)

    section_kinds = {section.kind for section in context.sections}
    assert {"role_instruction", "task_state", "output_contract", "operation_targets"}.issubset(
        section_kinds
    )
    assert context.estimated_tokens <= context.token_budget


def test_context_import_smoke() -> None:
    from restscope.context import ContextBuilder

    assert ContextBuilder is not None
