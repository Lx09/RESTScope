from __future__ import annotations

from types import SimpleNamespace


def _operation(operation_id: str, *, response: str = "200") -> dict:
    return {
        "operationId": operation_id,
        "summary": f"Summary for {operation_id}",
        "responses": {response: {"description": "response"}},
    }


def _schema(paths: dict) -> dict:
    return {
        "openapi": "3.0.3",
        "info": {"title": "Scheduler", "version": "1.0.0"},
        "paths": paths,
    }


def _request(spec: dict, *, headers: dict[str, str] | None = None):
    from restscope.agent import RESTScopeRunRequest

    return RESTScopeRunRequest(
        schema_source={"kind": "inline", "format": "json", "content": __import__("json").dumps(spec)},
        base_url="http://localhost:8000",
        headers=headers or {},
        allow_live_testing=True,
    )


def _graph(*, runner=None, analyzer=None):
    from restscope.agent import FakeOperationDependencyAnalyzer, FakeOperationTestRunner, RESTScopeMainGraph

    return RESTScopeMainGraph(
        operation_runner=runner or FakeOperationTestRunner(),
        dependency_analyzer=analyzer or FakeOperationDependencyAnalyzer(),
    )


def _ref(method: str, path: str, operation_id: str):
    from restscope.agent import OperationReference

    return OperationReference(method=method, path=path, operation_id=operation_id)


def _dependency(dependency, hint: str = "prerequisite"):
    from restscope.agent import OperationDependencyAnalysis

    return OperationDependencyAnalysis(dependency_issue=True, hint=hint, dependencies=[dependency])


def _clear_dependency():
    from restscope.agent import OperationDependencyAnalysis

    return OperationDependencyAnalysis(dependency_issue=False)


def test_supervisor_discovers_all_operations_and_stably_sorts_by_path_depth() -> None:
    from restscope.agent import FakeOperationDependencyAnalyzer, FakeOperationTestRunner

    spec = _schema(
        {
            "/deep/{id}": {"get": _operation("deep")},
            "/beta": {"get": _operation("beta")},
            "/alpha": {
                "post": _operation("alphaPost"),
                "get": _operation("alphaGet"),
            },
        }
    )
    runner = FakeOperationTestRunner()
    analyzer = FakeOperationDependencyAnalyzer()
    report = _graph(runner=runner, analyzer=analyzer).run(_request(spec))

    expected = [
        ("GET", "/beta"),
        ("POST", "/alpha"),
        ("GET", "/alpha"),
        ("GET", "/deep/{id}"),
    ]
    assert report.status == "passed"
    assert [(item.method, item.path) for item in report.operations] == expected
    assert [(item.operation.method, item.operation.path) for item in report.attempts] == expected
    assert [(call.method, call.path) for call in runner.calls] == expected
    assert len(analyzer.calls) == len(expected)
    assert report.rounds == 1
    assert report.attempt_count == 4
    assert len(report.satisfied_operations) == 4


def test_supervisor_request_contract_contains_only_mvp_entry_fields() -> None:
    from restscope.agent import RESTScopeRunRequest

    assert list(RESTScopeRunRequest.model_fields) == [
        "schema_source",
        "base_url",
        "headers",
        "allow_live_testing",
        "metadata",
    ]


def test_single_blocked_operation_is_retried_only_in_the_next_round() -> None:
    from restscope.agent import FakeOperationDependencyAnalyzer, FakeOperationTestRunner

    consumer = _ref("GET", "/consumer", "consumer")
    producer = _ref("POST", "/producer", "producer")
    analyzer = FakeOperationDependencyAnalyzer(
        analyses={
            ("GET", "/consumer"): [_dependency(producer), _clear_dependency()],
        }
    )
    runner = FakeOperationTestRunner()
    report = _graph(runner=runner, analyzer=analyzer).run(
        _request(_schema({"/consumer": {"get": _operation("consumer")}, "/producer": {"post": _operation("producer")}}))
    )

    assert report.status == "passed"
    assert [(attempt.operation.path, attempt.round_number, attempt.disposition) for attempt in report.attempts] == [
        ("/consumer", 1, "blocked"),
        ("/producer", 1, "satisfied"),
        ("/consumer", 2, "satisfied"),
    ]
    assert report.attempts[0].report.observed_2xx is True
    assert report.rounds == 2
    assert len(runner.calls) == 3


def test_multilevel_dependencies_unlock_one_round_at_a_time() -> None:
    from restscope.agent import FakeOperationDependencyAnalyzer

    first = _ref("POST", "/a", "a")
    second = _ref("POST", "/b", "b")
    analyzer = FakeOperationDependencyAnalyzer(
        analyses={
            ("GET", "/c"): [_dependency(second), _clear_dependency()],
            ("POST", "/b"): [_dependency(first), _clear_dependency()],
        }
    )
    report = _graph(analyzer=analyzer).run(
        _request(
            _schema(
                {
                    "/c": {"get": _operation("c")},
                    "/b": {"post": _operation("b")},
                    "/a": {"post": _operation("a")},
                }
            )
        )
    )

    assert report.status == "passed"
    assert [(item.operation.path, item.round_number) for item in report.attempts] == [
        ("/c", 1),
        ("/b", 1),
        ("/a", 1),
        ("/b", 2),
        ("/c", 3),
    ]
    assert report.rounds == 3


def test_unknown_dependency_stops_without_a_blind_retry() -> None:
    from restscope.agent import FakeOperationDependencyAnalyzer, OperationDependencyAnalysis

    analyzer = FakeOperationDependencyAnalyzer(
        analyses={
            ("GET", "/consumer"): OperationDependencyAnalysis(
                dependency_issue=True,
                hint="Some setup is missing",
                dependencies=[],
            )
        }
    )
    report = _graph(analyzer=analyzer).run(
        _request(_schema({"/consumer": {"get": _operation("consumer")}}))
    )

    assert report.status == "failed"
    assert report.stop_reason == "unresolved_dependencies"
    assert report.attempt_count == 1
    assert report.blocked_operations[0].reason == "unknown_dependency"
    assert report.error is None


def test_dependency_cycle_is_reported_without_retries() -> None:
    from restscope.agent import FakeOperationDependencyAnalyzer

    a = _ref("GET", "/a", "a")
    b = _ref("GET", "/b", "b")
    analyzer = FakeOperationDependencyAnalyzer(
        analyses={
            ("GET", "/a"): _dependency(b),
            ("GET", "/b"): _dependency(a),
        }
    )
    report = _graph(analyzer=analyzer).run(
        _request(_schema({"/a": {"get": _operation("a")}, "/b": {"get": _operation("b")}}))
    )

    assert report.status == "failed"
    assert report.stop_reason == "unresolved_dependencies"
    assert report.attempt_count == 2
    assert {item.reason for item in report.blocked_operations} == {"dependency_cycle"}
    assert [{operation.path for operation in cycle} for cycle in report.dependency_cycles] == [{"/a", "/b"}]


def test_failed_prerequisite_fails_fast_and_preserves_blocked_and_unattempted() -> None:
    from restscope.agent import FakeOperationDependencyAnalyzer, FakeOperationTestRunner, OperationExecutionResult

    prerequisite = _ref("POST", "/prerequisite", "prerequisite")
    analyzer = FakeOperationDependencyAnalyzer(
        analyses={("GET", "/consumer"): _dependency(prerequisite)}
    )
    runner = FakeOperationTestRunner(
        results={
            ("POST", "/prerequisite"): OperationExecutionResult(
                run_id="failed_prerequisite",
                outcome="failed",
                status_code_counts={"400": 1},
            )
        }
    )
    report = _graph(runner=runner, analyzer=analyzer).run(
        _request(
            _schema(
                {
                    "/consumer": {"get": _operation("consumer")},
                    "/prerequisite": {"post": _operation("prerequisite")},
                    "/later": {"get": _operation("later")},
                }
            )
        )
    )

    assert report.status == "failed"
    assert report.stop_reason == "operation_failed"
    assert [attempt.disposition for attempt in report.attempts] == ["blocked", "failed"]
    assert report.blocked_operations[0].reason == "failed_prerequisite"
    assert [operation.path for operation in report.unattempted_operations] == ["/later"]
    assert report.error is None


def test_ordinary_failure_and_technical_error_both_fail_fast_with_distinct_status() -> None:
    from restscope.agent import FakeOperationTestRunner, OperationExecutionResult

    spec = _schema({"/first": {"get": _operation("first")}, "/second": {"get": _operation("second")}})
    failed = _graph(
        runner=FakeOperationTestRunner(
            results={
                ("GET", "/first"): OperationExecutionResult(
                    run_id="failed",
                    outcome="passed",
                    status_code_counts={"404": 1},
                )
            }
        )
    ).run(_request(spec))
    errored = _graph(
        runner=FakeOperationTestRunner(error_paths={"/first"})
    ).run(_request(spec))

    assert (failed.status, failed.stop_reason, failed.error) == ("failed", "operation_failed", None)
    assert failed.attempts[0].disposition == "failed"
    assert [item.path for item in failed.unattempted_operations] == ["/second"]
    assert errored.status == "errored"
    assert errored.stop_reason == "technical_error"
    assert errored.error is not None
    assert errored.attempts[0].disposition == "errored"
    assert [item.path for item in errored.unattempted_operations] == ["/second"]


def test_schema_errors_and_missing_model_fail_before_live_requests() -> None:
    from restscope.agent import (
        DependencyAnalysisError,
        FakeOperationDependencyAnalyzer,
        FakeOperationTestRunner,
    )

    runner = FakeOperationTestRunner()
    invalid_schema = _graph(runner=runner).run(
        _request({"not": "openapi"})
    )
    missing_model = _graph(
        runner=runner,
        analyzer=FakeOperationDependencyAnalyzer(
            config_error=DependencyAnalysisError("Thinking model is not configured")
        ),
    ).run(_request(_schema({"/pets": {"get": _operation("pets")}})))

    assert invalid_schema.status == "errored"
    assert invalid_schema.error["stage"] == "discover_operations"
    assert missing_model.status == "errored"
    assert missing_model.error["stage"] == "validate_runtime"
    assert runner.calls == []


def test_file_url_and_inline_schema_sources_are_forwarded_to_parser(monkeypatch) -> None:
    from restscope.agent import RESTScopeRunRequest
    from restscope.openapi_parser import OpenAPIParser

    parsed = OpenAPIParser.parse(_schema({"/pets": {"get": _operation("pets")}}))
    seen = []
    monkeypatch.setattr(OpenAPIParser, "parse", staticmethod(lambda source: seen.append(source) or parsed))

    requests = [
        RESTScopeRunRequest(schema_source={"kind": "file", "path": "/tmp/api.yaml"}, allow_live_testing=True),
        RESTScopeRunRequest(
            schema_source={"kind": "url", "url": "https://example.test/api.yaml"},
            allow_live_testing=True,
        ),
        RESTScopeRunRequest(
            schema_source={"kind": "inline", "format": "yaml", "content": "openapi: 3.0.3"},
            allow_live_testing=True,
        ),
    ]
    for request in requests:
        assert _graph().run(request).status == "passed"

    assert seen == ["/tmp/api.yaml", "https://example.test/api.yaml", "openapi: 3.0.3"]


def test_headers_never_enter_graph_state_or_report() -> None:
    from restscope.agent import FakeOperationDependencyAnalyzer, FakeOperationTestRunner

    class RecordingRunner(FakeOperationTestRunner):
        def __init__(self):
            super().__init__()
            self.states = []

        def check_capabilities(self, *, target, state):
            self.states.append(state)
            return super().check_capabilities(target=target, state=state)

        def run_operation(self, *, target, state):
            self.states.append(state)
            assert target.headers == {"Authorization": "Bearer secret-token"}
            return super().run_operation(target=target, state=state)

    runner = RecordingRunner()
    report = _graph(runner=runner, analyzer=FakeOperationDependencyAnalyzer()).run(
        _request(
            _schema({"/pets": {"get": _operation("pets")}}),
            headers={"Authorization": "Bearer secret-token"},
        )
    )

    assert report.status == "passed"
    assert all("headers" not in state for state in runner.states)
    assert "secret-token" not in report.model_dump_json()


def test_restscope_app_runs_with_injected_runner_and_analyzer() -> None:
    from restscope import RESTScopeApp
    from restscope.agent import FakeOperationDependencyAnalyzer, FakeOperationTestRunner
    from restscope.restscope_config import RESTScopeConfig

    app = RESTScopeApp.from_config(
        RESTScopeConfig.from_environment(),
        operation_runner=FakeOperationTestRunner(),
        dependency_analyzer=FakeOperationDependencyAnalyzer(),
    )

    report = app.run(_request(_schema({"/pets": {"get": _operation("pets")}})))
    assert report.status == "passed"
    assert report.operations[0].method == "GET"


def test_restscope_app_context_manager_closes_mcp_host() -> None:
    from restscope import RESTScopeApp
    from restscope.agent import FakeOperationDependencyAnalyzer, FakeOperationTestRunner
    from restscope.restscope_config import RESTScopeConfig

    host = FakeMCPHost()
    runtime = SimpleNamespace(mcp_host=host)
    with RESTScopeApp.from_config(
        RESTScopeConfig.from_environment(),
        operation_runner=FakeOperationTestRunner(),
        dependency_analyzer=FakeOperationDependencyAnalyzer(),
        capability_runtime=runtime,
    ):
        assert host.closed is False
    assert host.closed is True


class FakeMCPHost:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True
