from __future__ import annotations

import json

import pytest


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
                            "input": "P1",
                            "confidence": 0.92,
                            "reason": "Generated project identifiers fail.",
                            "evidence": ["F1", "C1"],
                        }
                    ],
                }
            ),
            _response(
                {
                    "changes": [
                        {
                            "input": "P1",
                            "generation": {
                                "kind": "sample_values",
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
    first = client.requests[0].messages[1].content
    assert "Operation\nGET /projects/{projectId}" in first
    assert '[P1] required path parameter "projectId"' in first
    assert '[P2] optional query parameter "region"' in first
    assert "[F1] HTTP 404: Project not found" in first
    assert '[C1] P1="random-123"; P2=omitted; failures=F1' in first
    assert "input_node_id" not in first
    assert "config_revision" not in first
    assert "Authorization" not in first

    second = client.requests[1].messages[1].content
    assert "Suspected inputs" in second
    assert "evidence=F1,C1" in second
    assert "evidence=f1,case_1" not in second
    assert "[P1] Generated project identifiers fail." in second
    assert "random text, length 1 to 16" in second
    assert "failure_messages" not in second
    assert "random-123" not in second
    assert "input_node_id" not in second
    assert "reference_option_id" not in second
    assert all(request.response_format == "json" for request in client.requests)
    assert all(request.json_schema is None for request in client.requests)
    assert result.diagnosis.suspects[0].input_node_id == "path/projectId"
    assert result.diagnosis.suspects[0].evidence_refs == ["f1", "case_1"]
    assert result.updates[0].strategy.type == "choice"
    assert all(request.metadata["role"].startswith("operation_smoke_") for request in client.requests)


def test_prompt_alias_maps_are_read_only() -> None:
    from restscope.agent.operation_smoke.prompts import build_parameter_prompt

    prompt = build_parameter_prompt(_report(), _config())

    with pytest.raises(TypeError):
        prompt.input_by_alias["P1"] = "forged"  # type: ignore[index]
    with pytest.raises(TypeError):
        prompt.evidence_by_alias["F1"] = "forged"  # type: ignore[index]


def test_reference_generator_must_select_a_nonempty_system_option() -> None:
    from restscope.agent.operation_smoke import (
        AvailableReferenceOption,
        OperationSmokeDiagnoser,
    )

    client = StubLLMClient(
        [
            _response(
                {
                        "no_parameter_issue": False,
                        "suspects": [
                            {
                                "input": "P1",
                                "confidence": 0.95,
                                "reason": "The identifier must come from an existing project.",
                                "evidence": ["F1", "C1"],
                            }
                        ],
                    }
                ),
                _response(
                    {
                        "changes": [
                            {
                                "input": "P1",
                                "generation": {
                                    "kind": "observed_value",
                                    "source": "R1",
                                },
                            }
                        ],
                    }
            ),
        ]
    )
    option = AvailableReferenceOption(
        option_id="ref_project",
        input_node_id="path/projectId",
        kind="resource_identifier",
        canonical_resource="project",
        compatible_scalar_type="string",
        value_count=2,
        producer_operation_keys=["POST /projects"],
    )

    result = OperationSmokeDiagnoser(client=client, model=_model()).diagnose(
        report=_report(),
        config=_config(),
        reference_options=[option],
    )

    second = client.requests[1].messages[1].content
    assert (
        '[R1] for P1: observed identifiers for resource "project"; '
        "2 values available"
    ) in second
    assert "project-actual-secret" not in client.requests[1].messages[1].content
    assert "ref_project" not in second
    assert result.updates[0].model_dump(mode="json") == {
        "input_node_id": "path/projectId",
        "inclusion_probability": None,
        "strategy": {
            "type": "resource_identifier",
            "resource": "project",
        },
    }
    assert result.selected_reference_options == [option]


def test_round_two_repairs_forged_reference_option() -> None:
    from restscope.agent.operation_smoke import (
        AvailableReferenceOption,
        OperationSmokeDiagnoser,
    )

    client = StubLLMClient(
        [
            _response(
                {
                        "no_parameter_issue": False,
                        "suspects": [
                            {
                                "input": "P1",
                                "confidence": 0.95,
                                "reason": "An existing project is required.",
                                "evidence": ["F1", "C1"],
                            }
                        ],
                    }
                ),
                _response(
                    {
                        "changes": [
                            {
                                "input": "P1",
                                "generation": {
                                    "kind": "observed_value",
                                    "source": "R9",
                                },
                            }
                        ],
                    }
                ),
                _response(
                    {
                        "changes": [
                            {
                                "input": "P1",
                                "generation": {
                                    "kind": "observed_value",
                                    "source": "R1",
                                },
                            }
                        ],
                    }
            ),
        ]
    )

    result = OperationSmokeDiagnoser(client=client, model=_model()).diagnose(
        report=_report(),
        config=_config(),
        reference_options=[
            AvailableReferenceOption(
                option_id="ref_project",
                input_node_id="path/projectId",
                kind="resource_identifier",
                canonical_resource="project",
                compatible_scalar_type="string",
                value_count=1,
                producer_operation_keys=["POST /projects"],
            )
        ],
    )

    assert len(client.requests) == 3
    assert result.updates[0].strategy.type == "resource_identifier"


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
                                "input": "P9",
                                "confidence": 1,
                                "reason": "Guess.",
                                "evidence": ["F99"],
                            }
                        ],
                    }
            ),
            _response(
                {
                        "no_parameter_issue": False,
                        "suspects": [
                            {
                                "input": "P2",
                                "confidence": 0.8,
                                "reason": "The region input was omitted.",
                                "evidence": ["F1", "C1"],
                            }
                        ],
                    }
                ),
                _response(
                    {
                        "changes": [
                            {
                                "input": "P2",
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
    repair_prompt = client.requests[1].messages[-1].content
    assert "P9 was not offered" in repair_prompt
    assert "F99 was not supplied as evidence" in repair_prompt
    assert "input_node_id" not in repair_prompt
    assert "validation_errors" not in repair_prompt
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
                                "input": "P1",
                                "confidence": 0.9,
                                "reason": "Bad generated identifier.",
                                "evidence": ["F1", "C1"],
                            }
                        ],
                    }
                ),
                _response(
                    {
                        "changes": [
                            {
                                "input": "P2",
                                "inclusion_probability": 1,
                            }
                        ]
                }
            ),
                _response(
                    {
                        "changes": [
                            {
                                "input": "P1",
                                "generation": {
                                    "kind": "exact_value",
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
    repair_prompt = client.requests[2].messages[-1].content
    assert "P2 was not offered" in repair_prompt
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
    assert len(content.encode("utf-8")) <= MAX_FIRST_ROUND_USER_BYTES
    assert "Evidence truncated:" in content
    assert "context_truncated" not in content


@pytest.mark.parametrize(
    ("generation", "expected_type"),
    [
        ({"kind": "exact_value", "value": "known"}, "constant"),
        (
            {
                "kind": "sample_values",
                "values": ["known", "other"],
                "weights": [2, 1],
            },
            "choice",
        ),
        (
            {"kind": "integer_between", "minimum": 1, "maximum": 10},
            "integer_range",
        ),
        (
            {"kind": "number_between", "minimum": 0.5, "maximum": 2.5},
            "number_range",
        ),
        (
            {
                "kind": "random_text",
                "minimum_length": 2,
                "maximum_length": 8,
                "allowed_characters": "abc123",
            },
            "random_string",
        ),
        ({"kind": "boolean_bias", "true_probability": 0.75}, "boolean"),
        ({"kind": "formatted_value", "format": "uuid"}, "format"),
        (
            {"kind": "array_length", "minimum_items": 1, "maximum_items": 3},
            "array",
        ),
        ({"kind": "variant_weights", "weights": [1, 2]}, "variant"),
    ],
)
def test_generator_intents_compile_to_existing_generators(
    generation,
    expected_type,
) -> None:
    from restscope.agent.operation_smoke import OperationSmokeDiagnoser

    client = StubLLMClient(
        [
            _response(
                {
                    "no_parameter_issue": False,
                    "suspects": [
                        {
                            "input": "P1",
                            "confidence": 0.9,
                            "reason": "The current values fail.",
                            "evidence": ["F1", "C1"],
                        }
                    ],
                }
            ),
            _response(
                {
                    "changes": [
                        {
                            "input": "P1",
                            "generation": generation,
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

    assert result.updates[0].strategy.type == expected_type


def test_required_input_inclusion_is_repaired_with_semantic_alias_error() -> None:
    from restscope.agent.operation_smoke import OperationSmokeDiagnoser

    client = StubLLMClient(
        [
            _response(
                {
                    "no_parameter_issue": False,
                    "suspects": [
                        {
                            "input": "P1",
                            "confidence": 0.9,
                            "reason": "The current values fail.",
                            "evidence": ["F1", "C1"],
                        }
                    ],
                }
            ),
            _response(
                {
                    "changes": [
                        {
                            "input": "P1",
                            "inclusion_probability": 0.5,
                        }
                    ]
                }
            ),
            _response(
                {
                    "changes": [
                        {
                            "input": "P1",
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

    assert result.updates[0].inclusion_probability == 1
    repair_prompt = client.requests[-1].messages[-1].content
    assert "P1 is required" in repair_prompt
    assert "path/projectId" not in repair_prompt
