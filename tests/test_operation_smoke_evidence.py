"""Protect complete evidence expansion and the Plan-only alias boundary."""

from __future__ import annotations

import json
from types import SimpleNamespace

from restscope.agent.operation_smoke.agent import _operation_context
from restscope.agent.operation_smoke.evidence import (
    build_batch_evidence,
    build_plan_case_map,
)
from restscope.testing.execution import SmokeCaseExecutionEvidence
from tests._operation_smoke_plan_solve_fixtures import (
    smoke_config,
    smoke_report,
)


def test_batch_evidence_contains_generated_request_response_and_body() -> None:
    """Downstream roles receive useful evidence while credentials stay redacted."""
    report = smoke_report()

    batch = build_batch_evidence(
        report,
        {
            "case_1": SmokeCaseExecutionEvidence(
                case_id="case_1",
                response_body=b'{"error":"project missing"}',
                response_body_truncated=False,
                response_encoding="utf-8",
            )
        },
    )

    case = batch["cases"][0]
    assert case["generated_test_case"]["generated_values"][0]["value"] == (
        "random-123"
    )
    assert case["request"]["path"] == "/projects/random-123"
    assert case["request"]["headers"]["Authorization"] == "[redacted]"
    assert case["response"]["status_code"] == 404
    assert case["response"]["body"] == '{"error":"project missing"}'


def test_temporary_case_codes_exist_only_in_the_plan_projection() -> None:
    """The complete evidence itself never depends on C-style aliases."""
    batch = build_batch_evidence(smoke_report(), {})

    coded, failed = build_plan_case_map(batch)

    assert failed == ["C1"]
    assert coded["C1"]["case_id"] == "case_1"
    assert "C1" not in str(batch)


def test_failure_solve_operation_context_contains_complete_openapi_ir() -> None:
    """Real App contexts provide the full operation, not only Generator inputs."""
    from restscope.openapi_parser import OpenAPIParser

    ir = OpenAPIParser.parse(
        {
            "openapi": "3.0.3",
            "info": {"title": "Projects", "version": "1"},
            "paths": {
                "/projects/{projectId}": {
                    "get": {
                        "summary": "Fetch one project",
                        "parameters": [
                            {
                                "name": "projectId",
                                "in": "path",
                                "required": True,
                                "schema": {"type": "string"},
                            }
                        ],
                        "responses": {
                            "200": {
                                "description": "ok",
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "type": "object",
                                            "properties": {
                                                "id": {"type": "string"}
                                            },
                                        }
                                    }
                                },
                            }
                        },
                    }
                }
            },
        }
    )

    operation = _operation_context(
        SimpleNamespace(ir=ir),
        config=smoke_config(),
    )

    assert operation["openapi_operation_ir"]["summary"] == "Fetch one project"
    assert operation["openapi_operation_ir"]["responses"]["by_status"]["200"][
        "description"
    ] == "ok"
    assert operation["testing_snapshot"]["operation_key"] == (
        "GET /projects/{projectId}"
    )
    json.dumps(operation)
