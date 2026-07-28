"""Regression scenarios for project swagger smoke e2e live. Each test documents one observable contract or failure boundary."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import replace
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import time
from types import SimpleNamespace
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import ProxyHandler, build_opener
from uuid import uuid4

import pytest


pytestmark = pytest.mark.skip(
    reason=(
        "The former diagnosis/Group live contract is superseded. A bounded "
        "Plan/Solve/Patch/Effect live protocol requires separate user approval."
    )
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SPEC_PATH = PROJECT_ROOT / "assets" / "openapi" / "project_swagger.yaml"
DEFAULT_TARGET = "http://127.0.0.1:34985"
DEFAULT_PHOENIX = "http://127.0.0.1:6006"
EXPECTED_OPERATION_COUNT = 67
LIVE_OPERATION_COUNT = 10


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )


def _get_json(url: str, *, timeout: float = 10) -> dict[str, Any]:
    opener = build_opener(ProxyHandler({}))
    with opener.open(url, timeout=timeout) as response:  # noqa: S310 - explicit local endpoint.
        return json.load(response)


def _all_phoenix_spans(
    endpoint: str,
    project_name: str,
) -> list[dict[str, Any]]:
    base = (
        f"{endpoint.rstrip('/')}/v1/projects/"
        f"{quote(project_name, safe='')}/spans"
    )
    spans: list[dict[str, Any]] = []
    cursor: str | None = None
    seen_cursors: set[str] = set()
    for _page in range(100):
        query: dict[str, str | int] = {"limit": 1000}
        if cursor is not None:
            query["cursor"] = cursor
        payload = _get_json(f"{base}?{urlencode(query)}")
        spans.extend(payload.get("data", []))
        next_cursor = payload.get("next_cursor")
        if next_cursor is None:
            return spans
        if next_cursor in seen_cursors:
            raise AssertionError("Phoenix returned a repeating span cursor")
        seen_cursors.add(next_cursor)
        cursor = next_cursor
    raise AssertionError("Phoenix span pagination exceeded 100 pages")


def _wait_for_phoenix_spans(
    endpoint: str,
    project_name: str,
) -> list[dict[str, Any]]:
    deadline = time.monotonic() + 60
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            spans = _all_phoenix_spans(endpoint, project_name)
            if any(span["name"] == "RESTScopeApp.run" for span in spans):
                return spans
        except (
            HTTPError,
            OSError,
            URLError,
            TimeoutError,
            json.JSONDecodeError,
        ) as exc:
            last_error = exc
        time.sleep(0.5)
    raise AssertionError(
        "Phoenix did not return the project Swagger E2E trace within 60 seconds"
    ) from last_error


def _operation_key(operation: Any) -> str:
    return f"{operation.method.upper()} {operation.path}"


def _select_live_operation_keys(ir: Any) -> list[str]:
    """Choose a stable method-diverse 10-operation slice from the full asset."""

    remaining = {"GET": 4, "POST": 2, "PUT": 2, "DELETE": 2}
    selected: list[str] = []
    for operation_key, operation in ir.operations.items():
        method = operation.method.upper()
        if remaining.get(method, 0) <= 0:
            continue
        selected.append(operation_key)
        remaining[method] -= 1
    if len(selected) != LIVE_OPERATION_COUNT:
        raise AssertionError(
            "project_swagger.yaml no longer contains the expected method mix"
        )
    return selected


def _report_coverage(report: Any, expected: set[str]) -> dict[str, Any]:
    attempts: dict[str, list[Any]] = defaultdict(list)
    for attempt in report.attempts:
        attempts[_operation_key(attempt.operation)].append(attempt)
    rows = []
    for operation_key in sorted(expected):
        operation_attempts = attempts.get(operation_key, [])
        rows.append(
            {
                "operation_key": operation_key,
                "attempt_count": len(operation_attempts),
                "batch_count": sum(
                    len(item.smoke_result.batch_reports)
                    for item in operation_attempts
                ),
                "run_ids": [
                    batch.run_id
                    for item in operation_attempts
                    for batch in item.smoke_result.batch_reports
                ],
                "dispositions": [
                    item.disposition for item in operation_attempts
                ],
                "failure_kinds": [
                    item.failure_kind
                    for item in operation_attempts
                    if item.failure_kind is not None
                ],
            }
        )
    attempted = set(attempts)
    return {
        "expected_operation_count": len(expected),
        "attempted_operation_count": len(attempted & expected),
        "missing_operations": sorted(expected - attempted),
        "unexpected_operations": sorted(attempted - expected),
        "operations": rows,
    }


def _phoenix_summary(
    *,
    endpoint: str,
    project_name: str,
    spans: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "endpoint": endpoint,
        "project_name": project_name,
        "span_count": len(spans),
        "trace_ids": sorted(
            {span["context"]["trace_id"] for span in spans}
        ),
        "span_names": dict(
            sorted(Counter(span["name"] for span in spans).items())
        ),
        "span_kinds": dict(
            sorted(Counter(span["span_kind"] for span in spans).items())
        ),
        "status_codes": dict(
            sorted(Counter(span["status_code"] for span in spans).items())
        ),
    }


def _trace_contract_report(*, investigation_count: int = 1) -> Any:
    return SimpleNamespace(
        attempts=[
            SimpleNamespace(
                smoke_result=SimpleNamespace(
                    diagnoses=[
                        SimpleNamespace(
                            investigations=[object()] * investigation_count
                        )
                    ]
                )
            )
        ]
    )


def _diagnosis_protocol_report(
    *investigations: tuple[int, str],
) -> Any:
    return SimpleNamespace(
        attempts=[
            SimpleNamespace(
                smoke_result=SimpleNamespace(
                    diagnoses=[
                        SimpleNamespace(
                            investigations=[
                                SimpleNamespace(
                                    valid_outputs=valid_outputs,
                                    reason=reason,
                                )
                                for valid_outputs, reason in investigations
                            ]
                        )
                    ]
                    if investigations
                    else []
                )
            )
        ]
    )


def test_live_diagnosis_protocol_coverage_accepts_one_valid_decision() -> None:
    """Scenario: verify that live diagnosis protocol coverage accepts one valid decision."""
    _assert_live_diagnosis_protocol_coverage(
        _diagnosis_protocol_report(
            (0, "invalid_output_limit"),
            (1, "output_limit"),
        )
    )


def test_live_diagnosis_protocol_coverage_allows_no_failures() -> None:
    """Scenario: verify that live diagnosis protocol coverage allows no failures."""
    _assert_live_diagnosis_protocol_coverage(
        _diagnosis_protocol_report()
    )


def test_live_diagnosis_protocol_coverage_rejects_all_invalid_outputs() -> None:
    """Scenario: verify that live diagnosis protocol coverage rejects all invalid outputs."""
    with pytest.raises(AssertionError):
        _assert_live_diagnosis_protocol_coverage(
            _diagnosis_protocol_report(
                (0, "invalid_output_limit"),
                (0, "invalid_output_limit"),
            )
        )


def _diagnosis_trace_spans(
    *,
    investigation_parent: str = "diagnosis",
    include_llm: bool = True,
) -> list[dict[str, Any]]:
    operation_key = "GET /projects/{id}"
    spans = [
        {
            "name": "OperationSmokeDiagnoser.diagnose",
            "context": {"span_id": "diagnosis"},
            "parent_id": "smoke",
            "attributes": {"restscope.operation.key": operation_key},
        },
        {
            "name": "OperationSmokeDiagnoser.investigate_failure",
            "context": {"span_id": "investigation"},
            "parent_id": investigation_parent,
            "attributes": {"restscope.operation.key": operation_key},
        },
    ]
    if include_llm:
        spans.append(
            {
                "name": "LLMClient.invoke",
                "context": {"span_id": "llm"},
                "parent_id": "investigation",
                "attributes": {},
            }
        )
    return spans


def test_diagnosis_trace_contract_accepts_nested_investigation_llm() -> None:
    """Scenario: verify that diagnosis trace contract accepts nested investigation llm."""
    _assert_diagnosis_trace_hierarchy(
        spans=_diagnosis_trace_spans(),
        report=_trace_contract_report(),
    )


@pytest.mark.parametrize(
    ("investigation_parent", "include_llm"),
    [
        ("missing-diagnosis", True),
        ("diagnosis", False),
    ],
)
def test_diagnosis_trace_contract_rejects_incomplete_hierarchy(
    investigation_parent: str,
    include_llm: bool,
) -> None:
    """Scenario: verify that diagnosis trace contract rejects incomplete hierarchy."""
    with pytest.raises(AssertionError):
        _assert_diagnosis_trace_hierarchy(
            spans=_diagnosis_trace_spans(
                investigation_parent=investigation_parent,
                include_llm=include_llm,
            ),
            report=_trace_contract_report(),
        )


def _assert_live_diagnosis_protocol_coverage(report: Any) -> None:
    investigations = [
        investigation
        for attempt in report.attempts
        for diagnosis in attempt.smoke_result.diagnoses
        for investigation in diagnosis.investigations
    ]
    if not investigations:
        return
    assert any(
        investigation.valid_outputs > 0
        for investigation in investigations
    ), (
        "every live failure investigation ended without one valid "
        "FailureDecision"
    )
    assert any(
        investigation.reason != "invalid_output_limit"
        for investigation in investigations
    ), "every live failure investigation hit invalid_output_limit"


def _assert_diagnosis_trace_hierarchy(
    *,
    spans: list[dict[str, Any]],
    report: Any,
) -> None:
    by_id = {span["context"]["span_id"]: span for span in spans}
    diagnosis_spans = [
        span
        for span in spans
        if span["name"] == "OperationSmokeDiagnoser.diagnose"
    ]
    investigation_spans = [
        span
        for span in spans
        if span["name"] == "OperationSmokeDiagnoser.investigate_failure"
    ]
    report_diagnoses = [
        diagnosis
        for attempt in report.attempts
        for diagnosis in attempt.smoke_result.diagnoses
    ]

    assert len(diagnosis_spans) == len(report_diagnoses)
    assert len(investigation_spans) == sum(
        len(diagnosis.investigations) for diagnosis in report_diagnoses
    )
    for investigation_span in investigation_spans:
        parent = by_id.get(investigation_span["parent_id"])
        assert parent is not None
        assert parent["name"] == "OperationSmokeDiagnoser.diagnose"
        assert (
            parent["attributes"]["restscope.operation.key"]
            == investigation_span["attributes"]["restscope.operation.key"]
        )
        assert any(
            candidate["name"] == "LLMClient.invoke"
            and candidate["parent_id"]
            == investigation_span["context"]["span_id"]
            for candidate in spans
        )


def _assert_phoenix_coverage(
    *,
    spans: list[dict[str, Any]],
    report: Any,
    expected_operations: set[str],
    task_id: str,
) -> None:
    by_id = {span["context"]["span_id"]: span for span in spans}
    by_name: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for span in spans:
        by_name[span["name"]].append(span)

    app_spans = by_name["RESTScopeApp.run"]
    assert len(app_spans) == 1
    app_span = app_spans[0]
    assert app_span["parent_id"] is None
    assert app_span["attributes"]["restscope.task_id"] == task_id
    trace_id = app_span["context"]["trace_id"]
    assert {span["context"]["trace_id"] for span in spans} == {trace_id}

    graph_spans = by_name["RESTScopeMainGraph.run"]
    assert len(graph_spans) == 1
    assert graph_spans[0]["parent_id"] == app_span["context"]["span_id"]

    attempt_spans = by_name["RESTScopeMainGraph.operation_attempt"]
    smoke_spans = by_name["OperationSmokeAgent.run"]
    batch_spans = by_name["OperationTestingService.run_operation"]
    case_spans = by_name["RESTScopeTestCase.execute"]
    monitor_spans = by_name["APIBehaviorMonitorAgent.observe_response"]
    diagnosis_spans = by_name["OperationSmokeDiagnoser.diagnose"]
    probe_spans = by_name["restscope.http.request"]

    assert len(attempt_spans) == report.attempt_count
    assert len(smoke_spans) == report.attempt_count
    traced_operation_keys = {
        span["attributes"]["restscope.operation.key"]
        for span in attempt_spans
    }
    assert traced_operation_keys <= expected_operations
    assert len(traced_operation_keys) >= 10
    assert all(
        span["attributes"]["restscope.task_id"] == task_id
        for span in attempt_spans
    )
    assert all(
        by_id[span["parent_id"]]["name"] == "RESTScopeMainGraph.run"
        for span in attempt_spans
    )
    assert all(
        by_id[span["parent_id"]]["name"]
        == "RESTScopeMainGraph.operation_attempt"
        for span in smoke_spans
    )
    assert all(
        by_id[span["parent_id"]]["name"] == "OperationSmokeAgent.run"
        for span in batch_spans
    )
    assert all(
        by_id[span["parent_id"]]["name"]
        == "OperationTestingService.run_operation"
        for span in case_spans
    )
    case_monitor_spans = [
        span
        for span in monitor_spans
        if by_id[span["parent_id"]]["name"] == "RESTScopeTestCase.execute"
    ]
    probe_monitor_spans = [
        span
        for span in monitor_spans
        if by_id[span["parent_id"]]["name"] == "restscope.http.request"
    ]
    assert len(case_monitor_spans) + len(probe_monitor_spans) == len(
        monitor_spans
    )
    assert all(
        by_id[span["parent_id"]]["name"] == "OperationSmokeAgent.run"
        for span in diagnosis_spans
    )

    report_run_ids = {
        batch.run_id
        for attempt in report.attempts
        for batch in attempt.smoke_result.batch_reports
    }
    traced_run_ids = {
        span["attributes"]["restscope.test.run_id"]
        for span in batch_spans
    }
    assert traced_run_ids == report_run_ids
    cases_by_run = Counter(
        span["attributes"]["restscope.test.run_id"]
        for span in case_spans
    )
    assert set(cases_by_run) == report_run_ids
    assert all(case_count == 10 for case_count in cases_by_run.values())
    response_case_count = sum(
        "output.value" in span["attributes"] for span in case_spans
    )
    assert len(case_monitor_spans) == response_case_count
    assert len(probe_monitor_spans) == len(probe_spans)

    _assert_diagnosis_trace_hierarchy(
        spans=spans,
        report=report,
    )


@pytest.mark.live_e2e
def test_project_swagger_runs_every_operation_through_smoke_and_phoenix() -> None:
    """Scenario: verify that project swagger runs every operation through smoke and phoenix."""
    if os.environ.get("RUN_PROJECT_SWAGGER_SMOKE_E2E") != "1":
        pytest.skip("set RUN_PROJECT_SWAGGER_SMOKE_E2E=1 to run the live target")

    from restscope import RESTScopeApp
    from restscope.agent import RESTScopeRunRequest
    from restscope.openapi_parser import OpenAPIParser
    from restscope.restscope_config import (
        DBConfig,
        RESTScopeConfig,
        TracingConfig,
    )

    target = os.environ.get("RESTSCOPE_E2E_TARGET", DEFAULT_TARGET)
    phoenix = os.environ.get("RESTSCOPE_E2E_PHOENIX", DEFAULT_PHOENIX)
    env_file = Path(
        os.environ.get("RESTSCOPE_E2E_ENV_FILE", str(PROJECT_ROOT / ".env"))
    ).expanduser()
    artifact_root = Path(
        os.environ.get(
            "RESTSCOPE_E2E_ARTIFACT_DIR",
            str(PROJECT_ROOT / "artifacts" / "project-swagger-smoke-e2e"),
        )
    ).expanduser()
    max_operation_attempts = int(
        os.environ.get("RESTSCOPE_E2E_MAX_OPERATION_ATTEMPTS", "1")
    )
    run_id = (
        "project-swagger-smoke-"
        f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-"
        f"{uuid4().hex[:8]}"
    )
    run_dir = artifact_root / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    project_name = f"restscope-{run_id}"
    database = run_dir / "evidence.sqlite"

    ir = OpenAPIParser.parse(SPEC_PATH)
    assert len(ir.operations) == EXPECTED_OPERATION_COUNT
    selected_operation_keys = _select_live_operation_keys(ir)
    expected_operations = set(selected_operation_keys)
    config = RESTScopeConfig.from_environment(env_file)
    assert config.llm.fast.model, "FAST model is required for live Smoke diagnosis"
    assert config.llm.fast.api_key, "FAST API key is required for live Smoke diagnosis"
    config = replace(
        config,
        db=DBConfig(url=f"sqlite:///{database}"),
        tracing=TracingConfig(
            enabled=True,
            collector_endpoint=phoenix,
            project_name=project_name,
            api_key="",
            protocol="http/protobuf",
            batch=True,
            max_content_bytes=65536,
            flush_timeout_seconds=10,
        ),
    )
    _write_json(
        run_dir / "run-metadata.json",
        {
            "run_id": run_id,
            "started_at": datetime.now(UTC).isoformat(),
            "spec_path": str(SPEC_PATH),
            "target": target,
            "phoenix_endpoint": phoenix,
            "phoenix_project": project_name,
            "max_operation_attempts": max_operation_attempts,
            "source_operation_count": len(ir.operations),
            "selected_operation_count": len(expected_operations),
            "selected_operation_keys": selected_operation_keys,
            "fast_provider": config.llm.fast.provider,
            "fast_model": config.llm.fast.model,
        },
    )

    app: RESTScopeApp | None = None
    report = None
    run_error: Exception | None = None
    spans: list[dict[str, Any]] = []
    phoenix_error: Exception | None = None
    try:
        app = RESTScopeApp.from_config(config)
        assert app.tracing_runtime.enabled is True
        context = app.initialize(
            schema_source={"kind": "file", "path": str(SPEC_PATH)},
            base_url=target,
        )
        context.ir.operations = {
            operation_key: context.ir.operations[operation_key]
            for operation_key in selected_operation_keys
        }
        report = app.run(
            RESTScopeRunRequest(
                metadata={"task_id": run_id},
                max_operation_attempts=max_operation_attempts,
            )
        )
    except Exception as exc:
        run_error = exc
    finally:
        if app is not None:
            app.close()

    if report is not None:
        _write_json(
            run_dir / "report.json",
            report.model_dump(mode="json"),
        )
        _write_json(
            run_dir / "coverage.json",
            _report_coverage(report, expected_operations),
        )
    if run_error is not None:
        _write_json(
            run_dir / "run-error.json",
            {
                "type": type(run_error).__name__,
                "message": str(run_error),
            },
        )
    try:
        spans = _wait_for_phoenix_spans(phoenix, project_name)
    except Exception as exc:
        phoenix_error = exc
        _write_json(
            run_dir / "phoenix-error.json",
            {"type": type(exc).__name__, "message": str(exc)},
        )
    else:
        _write_json(
            run_dir / "phoenix-summary.json",
            _phoenix_summary(
                endpoint=phoenix,
                project_name=project_name,
                spans=spans,
            ),
        )

    if run_error is not None:
        raise run_error
    assert report is not None
    if phoenix_error is not None:
        raise phoenix_error

    _assert_live_diagnosis_protocol_coverage(report)
    coverage = _report_coverage(report, expected_operations)
    assert report.stop_reason in {"completed", "completed_with_failures"}
    assert report.status in {"passed", "failed"}
    assert report.unattempted_operations == []
    assert coverage["missing_operations"] == []
    assert coverage["unexpected_operations"] == []
    assert all(
        attempt.smoke_result.operation_key
        == _operation_key(attempt.operation)
        for attempt in report.attempts
    )
    smoke_tested = [
        row
        for row in coverage["operations"]
        if row["attempt_count"] >= 1
        and row["batch_count"] >= 1
        and row["run_ids"]
    ]
    assert len(smoke_tested) >= 10
    _assert_phoenix_coverage(
        spans=spans,
        report=report,
        expected_operations=expected_operations,
        task_id=run_id,
    )
