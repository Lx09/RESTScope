"""Shared fixtures for Operation Smoke Resolution and Patch tests."""

from __future__ import annotations


def smoke_config():
    from restscope.request_generation import (
        InputGeneratorConfig,
        InputNodeSnapshot,
        OperationGeneratorConfig,
        OperationTestSnapshot,
        SchemaSnapshot,
    )

    return OperationGeneratorConfig(
        operation_key="GET /projects/{projectId}",
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
    """Build one Catalog-ready failed Batch for workflow tests."""
    from restscope.harness.operation_testing.test_case_catalog import (
        CatalogTestCase,
        HTTPFailure,
    )
    from restscope.harness.operation_testing import BatchExecutionResult

    value = long_value or "random-123"
    return BatchExecutionResult(
        run_id="run_1",
        operation_key="GET /projects/{projectId}",
        seed=1,
        cases=(
            CatalogTestCase(
                case_id="TC1",
                request={
                    "path": {"projectId": value},
                    "query": {},
                    "header": {},
                    "cookie": {},
                },
                response_body={"message": "project missing"},
                failure=HTTPFailure(
                    status_code=404,
                    messages=["HTTP 404: project missing"],
                ),
            ),
        ),
    )


def request_body_date_config():
    """Build nested request-body inputs used by presence-closure regressions."""
    from restscope.openapi_parser import OpenAPIParser
    from restscope.request_generation.snapshot import build_initial_operation_config

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
