from __future__ import annotations

import json


class StubLLMClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def invoke(self, request):
        self.requests.append(request)
        return self.responses.pop(0)


def _model():
    from restscope.llm import LLMModelConfig

    return LLMModelConfig(
        role="operation_smoke_diagnosis",
        provider="stub",
        model="fast-model",
        max_tokens=2048,
    )


def _config():
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


def _report(*, long_value: str | None = None):
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

    case = TestCaseExecutionReport(
        case_id="case_1",
        generated_test_case=GeneratedTestCase(
            operation_key="GET /projects/{projectId}",
            case_index=0,
            path_parameters={"projectId": long_value or "random-123"},
            query_parameters={},
            header_parameters={},
            cookie_parameters={},
            generated_values=[
                GeneratedNodeValue(
                    input_node_id="path/projectId",
                    instance_path="path/projectId",
                    value=long_value or "random-123",
                )
            ],
            omitted_input_node_ids=["query/region"],
        ),
        request=PreparedRequestSummary(
            method="GET",
            path="/projects/random-123",
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


def _response(payload):
    from restscope.llm import LLMResponse

    return LLMResponse(provider="stub", model="fast-model", parsed_json=payload)


def test_two_round_diagnosis_separates_failed_values_from_current_generators() -> None:
    from restscope.agent.operation_smoke import OperationSmokeDiagnoser

    client = StubLLMClient(
        [
            _response(
                {
                    "no_parameter_issue": False,
                    "suspects": [
                        {
                            "input_node_id": "path/projectId",
                            "confidence": 0.92,
                            "reason": "Generated project identifiers fail.",
                            "evidence_refs": ["f1", "case_1"],
                        }
                    ],
                }
            ),
            _response(
                {
                    "updates": [
                        {
                            "input_node_id": "path/projectId",
                            "strategy": {
                                "type": "choice",
                                "values": ["known-project"],
                            },
                        }
                    ]
                }
            ),
        ]
    )

    result = OperationSmokeDiagnoser(client=client, model=_model()).diagnose(
        report=_report(),
        config=_config(),
    )

    assert len(client.requests) == 2
    first = json.loads(client.requests[0].messages[1].content)
    assert set(first) == {
        "failure_messages",
        "test_inputs",
        "context_truncated",
        "failure_message_count",
        "included_failure_message_count",
        "failed_case_count",
        "included_failed_case_count",
    }
    assert first["failure_messages"] == [
        {"failure_id": "f1", "message": "HTTP 404: Project not found"}
    ]
    assert first["test_inputs"] == [
        {
            "case_id": "case_1",
            "failure_message_ids": ["f1"],
            "values": {"path/projectId": "random-123"},
            "omitted_input_node_ids": ["query/region"],
        }
    ]
    assert "Generator" not in client.requests[0].messages[1].content
    assert "Authorization" not in client.requests[0].messages[1].content

    second = json.loads(client.requests[1].messages[1].content)
    assert set(second) == {"diagnosis", "current_generators"}
    assert "failure_messages" not in second
    assert "random-123" not in client.requests[1].messages[1].content
    assert second["current_generators"] == [
        {
            "input_node_id": "path/projectId",
            "inclusion_probability": 1.0,
            "strategy": {
                "type": "random_string",
                "min_length": 1,
                "max_length": 16,
                "alphabet": "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789",
            },
        }
    ]
    assert result.diagnosis.suspects[0].input_node_id == "path/projectId"
    assert result.updates[0].strategy.type == "choice"
    assert all(request.metadata["role"].startswith("operation_smoke_") for request in client.requests)


def test_no_parameter_issue_stops_before_generator_round() -> None:
    from restscope.agent.operation_smoke import OperationSmokeDiagnoser

    client = StubLLMClient(
        [_response({"no_parameter_issue": True, "suspects": []})]
    )

    result = OperationSmokeDiagnoser(client=client, model=_model()).diagnose(
        report=_report(),
        config=_config(),
    )

    assert len(client.requests) == 1
    assert result.diagnosis.no_parameter_issue is True
    assert result.updates == []


def test_round_one_repair_rejects_unknown_nodes_and_forged_evidence() -> None:
    from restscope.agent.operation_smoke import OperationSmokeDiagnoser

    client = StubLLMClient(
        [
            _response(
                {
                    "no_parameter_issue": False,
                    "suspects": [
                        {
                            "input_node_id": "query/unknown",
                            "confidence": 1,
                            "reason": "Guess.",
                            "evidence_refs": ["f99"],
                        }
                    ],
                }
            ),
            _response(
                {
                    "no_parameter_issue": False,
                    "suspects": [
                        {
                            "input_node_id": "query/region",
                            "confidence": 0.8,
                            "reason": "The region input was omitted.",
                            "evidence_refs": ["f1", "case_1"],
                        }
                    ],
                }
            ),
            _response(
                {
                    "updates": [
                        {
                            "input_node_id": "query/region",
                            "inclusion_probability": 1,
                        }
                    ]
                }
            ),
        ]
    )

    result = OperationSmokeDiagnoser(client=client, model=_model()).diagnose(
        report=_report(),
        config=_config(),
    )

    assert len(client.requests) == 3
    repair_payload = json.loads(client.requests[1].messages[-1].content)
    assert "validation_errors" in repair_payload
    assert result.updates[0].inclusion_probability == 1


def test_round_two_repair_rejects_updates_outside_suspects() -> None:
    from restscope.agent.operation_smoke import OperationSmokeDiagnoser

    client = StubLLMClient(
        [
            _response(
                {
                    "no_parameter_issue": False,
                    "suspects": [
                        {
                            "input_node_id": "path/projectId",
                            "confidence": 0.9,
                            "reason": "Bad generated identifier.",
                            "evidence_refs": ["f1", "case_1"],
                        }
                    ],
                }
            ),
            _response(
                {
                    "updates": [
                        {
                            "input_node_id": "query/region",
                            "inclusion_probability": 1,
                        }
                    ]
                }
            ),
            _response(
                {
                    "updates": [
                        {
                            "input_node_id": "path/projectId",
                            "strategy": {
                                "type": "constant",
                                "value": "known-project",
                            },
                        }
                    ]
                }
            ),
        ]
    )

    result = OperationSmokeDiagnoser(client=client, model=_model()).diagnose(
        report=_report(),
        config=_config(),
    )

    assert len(client.requests) == 3
    assert result.updates[0].input_node_id == "path/projectId"


def test_round_one_context_is_bounded_and_marks_deterministic_truncation() -> None:
    from restscope.agent.operation_smoke import (
        MAX_FIRST_ROUND_USER_BYTES,
        OperationSmokeDiagnoser,
    )

    client = StubLLMClient(
        [_response({"no_parameter_issue": True, "suspects": []})]
    )

    OperationSmokeDiagnoser(client=client, model=_model()).diagnose(
        report=_report(long_value="v" * (MAX_FIRST_ROUND_USER_BYTES * 2)),
        config=_config(),
    )

    content = client.requests[0].messages[1].content
    payload = json.loads(content)
    assert len(content.encode("utf-8")) <= MAX_FIRST_ROUND_USER_BYTES
    assert payload["context_truncated"] is True
