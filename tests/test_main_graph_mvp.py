from __future__ import annotations

from types import SimpleNamespace
from typing import Any


def test_main_graph_runs_selected_operations_in_order_with_fake_runner() -> None:
    from restscope.agent import (
        FakeOperationTestRunner,
        OperationSelection,
        RESTScopeMainGraph,
        RESTScopeRunRequest,
    )

    runner = FakeOperationTestRunner()
    graph = RESTScopeMainGraph(operation_runner=runner)

    report = graph.run(
        RESTScopeRunRequest(
            schema_source={"kind": "file", "path": "assets/openapi/petstore-v3.json"},
            base_url="http://localhost:8000",
            operations=[
                OperationSelection(method="GET", path="/pets", operation_id="listPets"),
                OperationSelection(method="POST", path="/pets", operation_id="createPet"),
            ],
            headers={"Authorization": "Bearer secret-token"},
            allow_live_testing=True,
        )
    )

    assert report.status == "passed"
    assert report.task_kind == "operation_test"
    assert [(item.method, item.path) for item in report.operations] == [
        ("GET", "/pets"),
        ("POST", "/pets"),
    ]
    assert len(report.operation_reports) == 2
    assert [call.method for call in runner.calls[:5]] == ["GET"] * 5
    assert [call.method for call in runner.calls[5:]] == ["POST"] * 5
    assert "secret-token" not in report.model_dump_json()


def test_main_graph_aggregates_failed_operation_findings() -> None:
    from restscope.agent import (
        FakeOperationTestRunner,
        OperationSelection,
        RESTScopeMainGraph,
        RESTScopeRunRequest,
    )

    report = RESTScopeMainGraph(operation_runner=FakeOperationTestRunner(failed_stage="conformance")).run(
        RESTScopeRunRequest(
            schema_source={"kind": "file", "path": "assets/openapi/petstore-v3.json"},
            operations=[OperationSelection(method="GET", path="/pets")],
            allow_live_testing=True,
        )
    )

    assert report.status == "failed"
    assert len(report.findings) == 1
    assert report.findings[0].stage == "conformance"


def test_main_graph_returns_errored_report_and_preserves_completed_operations() -> None:
    from restscope.agent import OperationSelection, RESTScopeMainGraph, RESTScopeRunRequest

    runner = PathSensitiveRunner(error_path="/pets/{petId}")
    report = RESTScopeMainGraph(operation_runner=runner).run(
        RESTScopeRunRequest(
            schema_source={"kind": "file", "path": "assets/openapi/petstore-v3.json"},
            operations=[
                OperationSelection(method="GET", path="/pets"),
                OperationSelection(method="GET", path="/pets/{petId}"),
            ],
            allow_live_testing=True,
        )
    )

    assert report.status == "errored"
    assert report.error is not None
    assert report.error["operation"]["path"] == "/pets/{petId}"
    assert len(report.operation_reports) == 1
    assert report.operation_reports[0].path == "/pets"


def test_main_graph_fails_when_no_operations_are_selected() -> None:
    from restscope.agent import RESTScopeMainGraph, RESTScopeRunRequest

    report = RESTScopeMainGraph(operation_runner=PathSensitiveRunner(error_path="/unused")).run(
        RESTScopeRunRequest(
            schema_source={"kind": "file", "path": "assets/openapi/petstore-v3.json"},
            operations=[],
            allow_live_testing=True,
        )
    )

    assert report.status == "errored"
    assert report.error is not None
    assert report.error["stage"] == "resolve_operations"


def test_restscope_app_from_config_runs_main_graph_with_injected_runner() -> None:
    from restscope import RESTScopeApp, RESTScopeRunRequest
    from restscope.agent import FakeOperationTestRunner, OperationSelection
    from restscope.restscope_config import RESTScopeConfig

    app = RESTScopeApp.from_config(
        RESTScopeConfig.from_environment(),
        operation_runner=FakeOperationTestRunner(),
    )

    report = app.run(
        RESTScopeRunRequest(
            schema_source={"kind": "file", "path": "assets/openapi/petstore-v3.json"},
            operations=[OperationSelection(method="GET", path="/pets")],
            allow_live_testing=True,
        )
    )

    assert report.status == "passed"
    assert report.operation_reports[0].method == "GET"


def test_restscope_app_context_manager_closes_mcp_host() -> None:
    from restscope import RESTScopeApp
    from restscope.agent import FakeOperationTestRunner
    from restscope.restscope_config import RESTScopeConfig

    host = FakeMCPHost()
    runtime = SimpleNamespace(mcp_host=host)

    with RESTScopeApp.from_config(
        RESTScopeConfig.from_environment(),
        operation_runner=FakeOperationTestRunner(),
        capability_runtime=runtime,
    ):
        assert host.closed is False

    assert host.closed is True


def test_restscope_app_import_smoke() -> None:
    from restscope import OperationSelection, RESTScopeApp, RESTScopeRunRequest

    assert RESTScopeApp is not None
    assert RESTScopeRunRequest is not None
    assert OperationSelection(method="GET", path="/pets").method == "GET"


class PathSensitiveRunner:
    def __init__(self, *, error_path: str) -> None:
        from restscope.agent import FakeOperationTestRunner

        self.error_path = error_path
        self.fake = FakeOperationTestRunner()

    @property
    def calls(self):
        return self.fake.calls

    def check_capabilities(self, *, target, state: dict[str, Any]) -> dict[str, Any]:
        return self.fake.check_capabilities(target=target, state=state)

    def run_stage(self, *, stage, target, options, state: dict[str, Any]):
        if target.path == self.error_path and stage.name == "smoke":
            raise RuntimeError("operation-specific failure")
        return self.fake.run_stage(stage=stage, target=target, options=options, state=state)


class FakeMCPHost:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True
