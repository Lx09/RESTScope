from __future__ import annotations

from pathlib import Path

import pytest


def test_operation_test_agent_runs_all_default_stages_with_fake_runner() -> None:
    from restscope.agent import FakeOperationTestRunner, OperationTestAgent, OperationTestRequest

    runner = FakeOperationTestRunner()
    agent = OperationTestAgent(runner=runner)

    report = agent.run(
        OperationTestRequest(
            schema_source={"kind": "file", "path": "assets/openapi/petstore-v3.json"},
            base_url="http://localhost:8000",
            method="get",
            path="/pets",
            headers={"Authorization": "Bearer secret-token"},
            allow_live_testing=True,
        )
    )

    assert report.status == "passed"
    assert report.method == "GET"
    assert report.path == "/pets"
    assert [stage.stage for stage in report.stages] == [
        "smoke",
        "conformance",
        "positive",
        "negative",
        "boundary",
    ]
    assert [call.stage for call in runner.calls] == [
        "smoke",
        "conformance",
        "positive",
        "negative",
        "boundary",
    ]
    assert "secret-token" not in report.model_dump_json()


def test_operation_test_agent_returns_fail_report_when_stage_fails() -> None:
    from restscope.agent import FakeOperationTestRunner, OperationTestAgent, OperationTestRequest

    runner = FakeOperationTestRunner(fail_stage="negative")
    agent = OperationTestAgent(runner=runner)

    report = agent.run(
        OperationTestRequest(
            schema_source={"kind": "file", "path": "assets/openapi/petstore-v3.json"},
            method="POST",
            path="/pets",
            allow_live_testing=True,
        )
    )

    assert report.status == "errored"
    assert report.error is not None
    assert report.error["stage"] == "negative"
    assert [stage.stage for stage in report.stages] == ["smoke", "conformance", "positive"]


def test_operation_test_agent_can_load_schema_and_operation_from_db(tmp_path: Path) -> None:
    from restscope.agent import FakeOperationTestRunner, OperationTestAgent, OperationTestRequest
    from restscope.db import Base, UnitOfWork, create_engine_from_url
    from restscope.db.session import make_session_factory

    engine = create_engine_from_url(f"sqlite:///{tmp_path / 'agent.db'}")
    Base.metadata.create_all(engine)
    session_factory = make_session_factory(engine)

    with UnitOfWork(session_factory) as uow:
        uow.schemas.add(
            id="schema_1",
            name="Petstore",
            version="1",
            spec_hash="hash_1",
            raw_spec_uri="assets/openapi/petstore-v3.json",
            normalized_spec_uri=None,
            openapi_version="3.0.0",
            operation_count=1,
        )
        uow.operations.add(
            id="op_1",
            schema_id="schema_1",
            operation_id="listPets",
            method="get",
            path="/pets",
            tags=[],
            summary="List pets",
            resource="pets",
            mutability="read",
            security=None,
            request_schema_refs=[],
            response_schema_refs=[],
            card_json={},
            static_risk_score=0,
        )
        uow.commit()

    report = OperationTestAgent(
        runner=FakeOperationTestRunner(),
        session_factory=session_factory,
    ).run(
        OperationTestRequest(
            schema_id="schema_1",
            operation_db_id="op_1",
            allow_live_testing=True,
        )
    )

    assert report.status == "passed"
    assert report.schema_id == "schema_1"
    assert report.operation_db_id == "op_1"
    assert report.operation_id == "listPets"
    assert report.method == "GET"
    assert report.path == "/pets"


def test_operation_test_report_records_findings_without_raw_artifact_body() -> None:
    from restscope.agent import FakeOperationTestRunner, OperationTestAgent, OperationTestRequest

    runner = FakeOperationTestRunner(failed_stage="conformance")
    report = OperationTestAgent(runner=runner).run(
        OperationTestRequest(
            schema_source={"kind": "file", "path": "assets/openapi/petstore-v3.json"},
            method="GET",
            path="/pets",
            allow_live_testing=True,
        )
    )

    payload = report.model_dump_json()

    assert report.status == "failed"
    assert report.findings
    assert report.findings[0].stage == "conformance"
    assert "raw_events" not in payload
    assert "full_response_body" not in payload


def test_operation_test_request_requires_direct_or_db_input() -> None:
    from pydantic import ValidationError

    from restscope.agent import OperationTestRequest

    with pytest.raises(ValidationError):
        OperationTestRequest(allow_live_testing=True)
