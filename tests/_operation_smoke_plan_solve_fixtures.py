"""Shared fixtures for Operation Smoke Plan & Solve tests."""

from __future__ import annotations


def smoke_config():
    from restscope.testing import (
        InputGeneratorConfig,
        InputNodeSnapshot,
        OperationGeneratorConfig,
        OperationTestSnapshot,
        SchemaSnapshot,
    )

    return OperationGeneratorConfig(
        operation_key="GET /projects/{projectId}",
        revision=3,
        snapshot=OperationTestSnapshot(
            operation_key="GET /projects/{projectId}",
            method="GET",
            path="/projects/{projectId}",
            parameters=[],
            input_nodes=[
                InputNodeSnapshot(
                    input_node_id="path/projectId",
                    node_kind="parameter",
                    canonical_path="path/projectId",
                    required=True,
                    schema_contract=SchemaSnapshot(type="string"),
                ),
                InputNodeSnapshot(
                    input_node_id="query/region",
                    node_kind="parameter",
                    canonical_path="query/region",
                    required=False,
                    schema_contract=SchemaSnapshot(type="string"),
                ),
            ],
        ),
        configs=[
            InputGeneratorConfig(
                input_node_id="path/projectId",
                inclusion_probability=1,
                strategy={
                    "type": "random_string",
                    "min_length": 1,
                    "max_length": 16,
                },
            ),
            InputGeneratorConfig(
                input_node_id="query/region",
                inclusion_probability=0.5,
                strategy={"type": "choice", "values": ["us-east"]},
            ),
        ],
    )


def smoke_report(*, long_value: str | None = None):
    from restscope.testing import (
        BatchFailureReport,
        GeneratedNodeValue,
        GeneratedTestCase,
        OperationExecutionReport,
        UniqueFailureMessage,
    )
    from restscope.testing.models import (
        PreparedRequestSummary,
        ResponseSummary,
        TestCaseExecutionReport,
    )

    value = long_value or "random-123"
    case = TestCaseExecutionReport(
        case_id="case_1",
        generated_test_case=GeneratedTestCase(
            operation_key="GET /projects/{projectId}",
            case_index=0,
            path_parameters={"projectId": value},
            query_parameters={},
            header_parameters={},
            cookie_parameters={},
            generated_values=[
                GeneratedNodeValue(
                    input_node_id="path/projectId",
                    instance_path="path/projectId",
                    value=value,
                )
            ],
            omitted_input_node_ids=["query/region"],
        ),
        request=PreparedRequestSummary(
            method="GET",
            path=f"/projects/{value}",
            query_items=[],
            headers={"Authorization": "must-not-enter-the-prompt"},
            body_size_bytes=0,
        ),
        response=ResponseSummary(
            status_code=404,
            reason_phrase="Not Found",
            media_type="application/json",
            latency_ms=1,
        ),
    )
    return OperationExecutionReport(
        run_id="run_1",
        operation_key="GET /projects/{projectId}",
        seed=1,
        config_revision=3,
        status="completed",
        cases=[case],
        status_code_counts={"404": 1},
        error_count=0,
        observed_2xx=False,
        failure_report=BatchFailureReport(
            unique_failure_messages=[
                UniqueFailureMessage(
                    failure_id="f1",
                    message="HTTP 404: Project not found",
                    case_ids=["case_1"],
                )
            ]
        ),
    )


def request_body_date_config():
    """Build nested request-body inputs used by presence-closure regressions."""
    from restscope.openapi_parser import OpenAPIParser
    from restscope.testing.snapshot import build_initial_operation_config

    operation = OpenAPIParser.parse(
        {
            "openapi": "3.0.3",
            "info": {"title": "Assignments", "version": "1"},
            "paths": {
                "/assignments": {
                    "post": {
                        "requestBody": {
                            "required": True,
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "required": [
                                            "commitDate",
                                            "employee",
                                            "project",
                                        ],
                                        "properties": {
                                            "commitDate": {
                                                "type": "string",
                                                "format": "date-time",
                                            },
                                            "employee": {
                                                "type": "object",
                                                "required": ["hiredate"],
                                                "properties": {
                                                    "hiredate": {
                                                        "type": "string",
                                                        "format": "date",
                                                    }
                                                },
                                            },
                                            "project": {
                                                "type": "object",
                                                "required": [
                                                    "startDate",
                                                    "endDate",
                                                ],
                                                "properties": {
                                                    "startDate": {
                                                        "type": "string",
                                                        "format": "date",
                                                    },
                                                    "endDate": {
                                                        "type": "string",
                                                        "format": "date",
                                                    },
                                                },
                                            },
                                        },
                                    }
                                },
                                "application/xml": {
                                    "schema": {"type": "string"}
                                },
                            },
                        },
                        "responses": {"200": {"description": "ok"}},
                    }
                }
            },
        }
    ).operations["POST /assignments"]
    return build_initial_operation_config(operation)
