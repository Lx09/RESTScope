"""Root-cause Plan & Solve contracts for Operation Smoke."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from tests._operation_smoke_plan_solve_fixtures import (
    smoke_config,
    smoke_report,
)


class StubClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def invoke(self, request):
        self.requests.append(request)
        return self.responses.pop(0)


def llm_response(payload=None, *, tool_calls=None):
    from restscope.llm import LLMResponse

    return LLMResponse(
        provider="stub",
        model="model",
        parsed_json=payload,
        tool_calls=tool_calls or [],
    )


def planning_model():
    from restscope.llm import LLMModelConfig

    return LLMModelConfig(
        role="operation_smoke_root_cause_diagnosis",
        provider="stub",
        model="think-model",
        reasoning={"mode": "enabled", "effort": "high"},
    )


def test_model_selector_has_independent_smoke_phase_roles() -> None:
    """Scenario: verify that model selector has independent smoke phase roles."""
    from restscope.llm import LLMModelConfig, ModelSelector

    selector = ModelSelector(
        thinking=LLMModelConfig(
            role="thinking",
            provider="stub",
            model="think-model",
            temperature=0.7,
        ),
        fast=LLMModelConfig(
            role="fast",
            provider="stub",
            model="fast-model",
            temperature=0.7,
        ),
    )

    for role, expected_model in (
        ("operation_smoke_root_cause_diagnosis", "think-model"),
        ("parameter_patch_agent", "fast-model"),
        ("operation_smoke_effect_validation", "think-model"),
    ):
        selected = selector.select(role)
        assert selected.model == expected_model
        assert selected.temperature == 0
    with pytest.raises(ValueError, match="Unsupported LLM role"):
        selector.select("operation_smoke_patch_grouping")


def ready_decision():
    return {
        "action": "ready",
        "cause": "The generated project identifier does not exist.",
        "solutions": [
            {
                "input": "path.projectId",
                "desired_behavior": "Use an identifier accepted by the API.",
                "candidate_values": ["known-project"],
            }
        ],
        "evidence_refs": ["F1", "C1"],
        "interaction_notes": [],
    }


def hypothesis_decision():
    return {
        "action": "hypothesis",
        "hypothesis": "The generated project identifier does not exist.",
        "target_inputs": ["path.projectId"],
        "proposed_changes": ["Use a known project identifier."],
        "expected_outcome": "The request no longer returns project not found.",
        "evidence_refs": ["F1", "C1"],
    }


def confirmed_decision():
    decision = ready_decision()
    decision["action"] = "confirmed"
    decision["evidence_refs"] = ["F1", "C1", "O1", "O5"]
    return decision


def test_failure_decision_protocol_examples_match_the_dto() -> None:
    """Scenario: verify that failure decision protocol examples match the dto."""
    from restscope.agent.operation_smoke.planning import (
        FailureDecision,
        build_failure_decision_protocol,
    )

    protocol = build_failure_decision_protocol(
        input_handle="path.projectId",
        failure_ref="F1",
        observation_ref="O1",
    )

    assert protocol.allowed_fields == tuple(FailureDecision.model_fields)
    assert set(protocol.examples) == {
        "ready",
        "hypothesis",
        "confirmed",
        "deferred",
    }
    for example in protocol.examples.values():
        decision = FailureDecision.model_validate(example)
        assert decision.semantic_errors() == []


def test_failure_decision_protocol_hides_confirmed_without_observation() -> None:
    """Scenario: verify that failure decision protocol hides confirmed without observation."""
    from restscope.agent.operation_smoke.planning import (
        build_failure_decision_protocol,
    )

    protocol = build_failure_decision_protocol(
        input_handle="path.projectId",
        failure_ref="F1",
    )

    assert "confirmed" not in protocol.examples
    assert "action=confirmed is unavailable" in protocol.text


def test_effect_decision_protocol_matches_the_dto() -> None:
    """Scenario: verify that effect decision protocol matches the dto."""
    from restscope.agent.operation_smoke.prompts import (
        PatchItemValidationDecision,
        PatchValidationDecision,
        build_patch_validation_decision_protocol,
    )

    protocol = build_patch_validation_decision_protocol(
        target_refs=["F1", "F2"],
        candidate_failure_refs=["CF1"],
    )

    assert protocol.allowed_fields == tuple(
        PatchValidationDecision.model_fields
    )
    assert protocol.item_allowed_fields == tuple(
        PatchItemValidationDecision.model_fields
    )
    decision = PatchValidationDecision.model_validate(protocol.example)
    assert [item.item_id for item in decision.items] == ["F1", "F2"]
    assert '"items":[' in protocol.text
    assert "Never return initial failure refs (F1, F2)" in protocol.text


def test_failure_prompt_and_repair_share_the_complete_dto_protocol() -> None:
    """Scenario: verify that failure prompt and repair share the complete dto protocol."""
    from restscope.agent.operation_smoke.evidence import EvidenceJournal
    from restscope.agent.operation_smoke.prompts import (
        build_failure_investigation_prompt,
    )

    config = smoke_config()
    journal = EvidenceJournal.from_batch(
        report=smoke_report(),
        config=config,
    )

    prompt = build_failure_investigation_prompt(
        config=config,
        journal=journal,
        failure_ref="F1",
        root_failure_refs=["F1"],
        active_hypothesis=None,
    )

    for content in (prompt.system, prompt.repair_guidance):
        assert "Allowed top-level keys (exactly)" in content
        assert '"solutions"' in content
        assert '"evidence_refs"' in content
        assert '"proposed_changes"' in content
        assert "proposed_changes must be a JSON array of strings" in content
        assert "Never return failure_ref, explanation, reasoning, or " in content


def test_repair_recovers_the_live_invalid_hypothesis_shape() -> None:
    """Scenario: verify that repair recovers the live invalid hypothesis shape."""
    from restscope.agent.operation_smoke import OperationSmokeDiagnoser

    invalid_live_shape = {
        "action": "hypothesis",
        "failure_ref": "F1",
        "explanation": "The generated identifier must be numeric.",
        "target_inputs": ["path.projectId"],
        "proposed_changes": {
            "input": "path.projectId",
            "behavior": "Generate an integer identifier.",
        },
        "expected_outcome": "The request no longer returns HTTP 400.",
    }
    client = StubClient(
        [
            llm_response(invalid_live_shape),
            llm_response(hypothesis_decision()),
        ]
    )

    result = OperationSmokeDiagnoser(
        client=client,
        planning_model=planning_model(),
    ).diagnose(
        report=smoke_report(),
        config=smoke_config(),
        max_diagnosis_outputs_per_failure=1,
    )

    assert result.valid_outputs == 1
    assert result.invalid_outputs == 1
    assert result.deferred_failures[0].reason == "output_limit"
    repair_message = client.requests[1].messages[-1].content
    assert "Allowed top-level keys (exactly)" in repair_message
    assert "proposed_changes must be a JSON array of strings" in repair_message
    assert "Never return failure_ref, explanation, reasoning, or " in repair_message


class Probe:
    def __init__(self):
        self.calls = []

    def tool_spec(self, config):
        from restscope.llm import ToolSpec

        return ToolSpec(
            name="restscope.http.request",
            description="current operation only",
            kind="local_function",
            input_schema={"type": "object"},
            risk_level="high",
            read_only=False,
        )

    def validate(self, *, config, tool_call):
        if tool_call.name != "restscope.http.request":
            return "Only restscope.http.request may be called."
        return None

    def execute(self, *, config, tool_call):
        from restscope.llm import ToolResult

        self.calls.append(tool_call)
        return ToolResult(
            tool_call_id=tool_call.id,
            name=tool_call.name,
            status="succeeded",
            structured={"status_code": 200, "body": {"id": "known-project"}},
        )


def test_request_replaces_global_plan_and_http_budgets() -> None:
    """Scenario: verify that request replaces global plan and http budgets."""
    from restscope.agent.operation_smoke import (
        OperationSmokeDiagnoser,
        OperationSmokeRequest,
    )

    request = OperationSmokeRequest(operation_key="GET /items")

    assert request.max_diagnosis_outputs_per_failure == 20
    assert request.max_patch_attempts == 20
    assert not hasattr(request, "max_planning_outputs")
    assert not hasattr(request, "max_http_tool_rounds")
    assert not hasattr(OperationSmokeDiagnoser, "validate_patch")

    with pytest.raises(ValidationError):
        OperationSmokeRequest(
            operation_key="GET /items",
            max_diagnosis_outputs_per_failure=21,
        )
    with pytest.raises(ValidationError):
        OperationSmokeRequest(
            operation_key="GET /items",
            max_patch_attempts=1,
        )


def test_ready_failure_becomes_actionable_without_http_probe() -> None:
    """Scenario: verify that ready failure becomes actionable without http probe."""
    from restscope.agent.operation_smoke import OperationSmokeDiagnoser

    client = StubClient([llm_response(ready_decision())])
    diagnoser = OperationSmokeDiagnoser(
        client=client,
        planning_model=planning_model(),
    )

    result = diagnoser.diagnose(
        report=smoke_report(),
        config=smoke_config(),
    )

    assert result.status == "actionable"
    assert result.termination_reason == "all_failures_processed"
    assert result.http_tool_calls == 0
    assert result.valid_outputs == 1
    assert len(result.actionable_failures) == 1
    actionable = result.actionable_failures[0]
    assert actionable.failure_ref == "F1"
    assert actionable.root_failure_refs == ["F1"]
    assert actionable.evidence_origin == "initial"
    assert actionable.affected_inputs == ["path.projectId"]
    assert result.investigations[0].status == "ready"
    assert len(client.requests) == 1
    assert client.requests[0].tools == []


def test_hypothesis_executes_many_probes_then_confirms_from_observations() -> None:
    """Scenario: verify that hypothesis executes many probes then confirms from observations."""
    from restscope.agent.operation_smoke import OperationSmokeDiagnoser
    from restscope.llm import ToolCall

    calls = [
        ToolCall(
            id=f"probe-{index}",
            name="restscope.http.request",
            arguments={
                "method": "GET",
                "path": f"/projects/known-{index}",
            },
        )
        for index in range(1, 6)
    ]
    probe = Probe()
    client = StubClient(
        [
            llm_response(hypothesis_decision()),
            llm_response(tool_calls=calls),
            llm_response(confirmed_decision()),
        ]
    )

    result = OperationSmokeDiagnoser(
        client=client,
        planning_model=planning_model(),
        http_probe=probe,
    ).diagnose(report=smoke_report(), config=smoke_config())

    assert result.status == "actionable"
    assert result.valid_outputs == 3
    assert result.http_tool_calls == 5
    assert len(probe.calls) == 5
    assert result.investigations[0].status == "confirmed"
    assert result.actionable_failures[0].evidence_origin == "probe"
    assert len(client.requests[1].tools) == 1
    assert client.requests[1].tool_choice == "auto"


def test_probe_failure_uses_batch_failure_signature_for_reproduction() -> None:
    """Scenario: verify that probe failure uses batch failure signature for reproduction."""
    from restscope.agent.operation_smoke.evidence import EvidenceJournal
    from restscope.llm import ToolCall, ToolResult

    journal = EvidenceJournal.from_batch(
        report=smoke_report(),
        config=smoke_config(),
    )
    observation_ref, failure_ref = journal.record_tool_result(
        ToolCall(
            id="probe-reproduced",
            name="restscope.http.request",
            arguments={
                "method": "GET",
                "path": "/projects/random-123",
            },
        ),
        ToolResult(
            tool_call_id="probe-reproduced",
            name="restscope.http.request",
            status="succeeded",
            structured={
                "status_code": 404,
                "reason_phrase": "Not Found",
                "headers": {"content-type": "application/json"},
                "body_format": "json",
                "body": {"message": "Project not found"},
            },
        ),
    )

    assert failure_ref == "F1"
    assert journal.observation_reproduces(observation_ref, "F1")


def test_diagnosis_prompt_compacts_all_ten_cases_with_bounded_bodies() -> None:
    """Scenario: verify that diagnosis prompt compacts all ten cases with bounded bodies."""
    import json

    from restscope.agent.operation_smoke.evidence import EvidenceJournal
    from restscope.testing import UniqueFailureMessage

    base = smoke_report()
    cases = []
    private = {}
    for index in range(10):
        case_id = f"case_{index}"
        cases.append(
            base.cases[0].model_copy(
                update={
                    "case_id": case_id,
                    "generated_test_case": base.cases[
                        0
                    ].generated_test_case.model_copy(
                        update={"case_index": index}
                    ),
                }
            )
        )
        private[case_id] = {
            "response_body": (
                b'{"message":"' + (b"x" * 8192) + b'"}'
            ),
            "response_body_truncated": False,
            "response_encoding": "utf-8",
            "behavior_monitor": {
                "duplicate": "must not enter compact prompt"
            },
        }
    report = base.model_copy(
        update={
            "cases": cases,
            "failure_report": base.failure_report.model_copy(
                update={
                    "unique_failure_messages": [
                        UniqueFailureMessage(
                            failure_id="f1",
                            message="HTTP 404: Project not found",
                            case_ids=[case.case_id for case in cases],
                        )
                    ]
                }
            ),
        }
    )

    journal = EvidenceJournal.from_batch(
        report=report,
        config=smoke_config(),
        private_case_evidence=private,
    )
    records = journal.prompt_records()
    case_records = [record for record in records if record["kind"] == "case"]

    assert len(case_records) == 10
    assert len(
        json.dumps(
            records,
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ) <= 64 * 1024
    for record in case_records:
        value = record["value"]
        assert "private_response" not in value
        assert "behavior_monitor" not in value
        assert "headers" not in value["request"]
        assert value["response"]["status_code"] == 404
        assert value["response"]["body"] is not None
        assert len(
            json.dumps(
                value["response"]["body"],
                ensure_ascii=False,
                default=str,
            ).encode("utf-8")
        ) <= 4 * 1024 + 512


def test_disproved_hypothesis_is_replaced_before_confirmation() -> None:
    """Scenario: verify that disproved hypothesis is replaced before confirmation."""
    from restscope.agent.operation_smoke import OperationSmokeDiagnoser
    from restscope.llm import ToolCall, ToolResult

    class SequencedProbe(Probe):
        def execute(self, *, config, tool_call):
            self.calls.append(tool_call)
            if len(self.calls) == 1:
                return ToolResult(
                    tool_call_id=tool_call.id,
                    name=tool_call.name,
                    status="succeeded",
                    structured={
                        "status_code": 404,
                        "reason_phrase": "Not Found",
                        "body": {"message": "Project not found"},
                    },
                )
            return ToolResult(
                tool_call_id=tool_call.id,
                name=tool_call.name,
                status="succeeded",
                structured={"status_code": 200, "body": {"id": "project-2"}},
            )

    replacement = hypothesis_decision()
    replacement["hypothesis"] = (
        "The identifier must come from the observed project pool."
    )
    replacement["proposed_changes"] = [
        "Use an observed project pool value."
    ]
    replacement["evidence_refs"] = ["F1", "C1", "O1"]
    confirmed = confirmed_decision()
    confirmed["evidence_refs"] = ["F1", "C1", "O1"]
    client = StubClient(
        [
            llm_response(hypothesis_decision()),
            llm_response(
                tool_calls=[
                    ToolCall(
                        id="probe-1",
                        name="restscope.http.request",
                        arguments={"method": "GET", "path": "/projects/one"},
                    )
                ]
            ),
            llm_response(replacement),
            llm_response(confirmed),
            llm_response(
                {
                    "action": "deferred",
                    "reason": "The diagnostic probe failure is not actionable.",
                }
            ),
        ]
    )

    result = OperationSmokeDiagnoser(
        client=client,
        planning_model=planning_model(),
        http_probe=SequencedProbe(),
    ).diagnose(report=smoke_report(), config=smoke_config())

    assert result.status == "actionable"
    assert result.investigations[0].hypothesis_count == 2
    assert result.investigations[0].valid_outputs == 4
    assert result.investigations[0].http_tool_calls == 1
    replacement_prompt = client.requests[3].messages[-1].content
    assert '"inherited_observation_refs": [' in replacement_prompt
    assert '"O1"' in replacement_prompt
    assert '"probe_observation_refs": []' in replacement_prompt


def test_repair_keeps_http_tool_available_for_active_hypothesis() -> None:
    """Scenario: verify that repair keeps http tool available for active hypothesis."""
    from restscope.agent.operation_smoke import OperationSmokeDiagnoser
    from restscope.llm import ToolCall

    client = StubClient(
        [
            llm_response(hypothesis_decision()),
            llm_response(
                {
                    **confirmed_decision(),
                    "evidence_refs": ["F1", "C1"],
                }
            ),
            llm_response(
                tool_calls=[
                    ToolCall(
                        id="repair-probe",
                        name="restscope.http.request",
                        arguments={
                            "method": "GET",
                            "path": "/projects/known",
                        },
                    )
                ]
            ),
            llm_response(
                {
                    **confirmed_decision(),
                    "evidence_refs": ["F1", "C1", "O1"],
                }
            ),
        ]
    )

    result = OperationSmokeDiagnoser(
        client=client,
        planning_model=planning_model(),
        http_probe=Probe(),
    ).diagnose(report=smoke_report(), config=smoke_config())

    assert result.status == "actionable"
    assert client.requests[2].tool_choice == "auto"
    assert len(client.requests[2].tools) == 1
    assert result.http_tool_calls == 1


def test_invalid_tool_call_prevents_the_entire_tool_batch_from_executing() -> None:
    """Scenario: verify that invalid tool call prevents the entire tool batch from executing."""
    from restscope.agent.operation_smoke import OperationSmokeDiagnoser
    from restscope.llm import ToolCall

    probe = Probe()
    client = StubClient(
        [
            llm_response(hypothesis_decision()),
            llm_response(
                tool_calls=[
                    ToolCall(
                        id="valid",
                        name="restscope.http.request",
                        arguments={
                            "method": "GET",
                            "path": "/projects/known",
                        },
                    ),
                    ToolCall(
                        id="invalid",
                        name="another.tool",
                        arguments={},
                    ),
                ]
            ),
            llm_response(
                {
                    "action": "deferred",
                    "reason": "No safe probe is available.",
                }
            ),
        ]
    )

    result = OperationSmokeDiagnoser(
        client=client,
        planning_model=planning_model(),
        http_probe=probe,
    ).diagnose(report=smoke_report(), config=smoke_config())

    assert probe.calls == []
    assert result.status == "inconclusive"
    assert result.valid_outputs == 2
    assert result.invalid_outputs == 1
    assert result.investigations[0].status == "deferred"


def test_http_probe_preflight_rejects_invalid_argument_structure() -> None:
    """Scenario: verify that http probe preflight rejects invalid argument structure."""
    from restscope.agent.operation_smoke.probe import CurrentOperationHTTPProbe
    from restscope.llm import ToolCall

    probe = CurrentOperationHTTPProbe(executor=object())
    error = probe.validate(
        config=smoke_config(),
        tool_call=ToolCall(
            id="invalid-arguments",
            name="restscope.http.request",
            arguments={
                "method": "GET",
                "path": "/projects/known",
                "unexpected": True,
            },
        ),
    )

    assert error is not None
    assert "unexpected" in error


def test_failures_are_investigated_serially_in_first_seen_order() -> None:
    """Scenario: verify that failures are investigated serially in first seen order."""
    from restscope.agent.operation_smoke import OperationSmokeDiagnoser
    from restscope.testing import UniqueFailureMessage

    report = smoke_report()
    report = report.model_copy(
        update={
            "failure_report": report.failure_report.model_copy(
                update={
                    "unique_failure_messages": [
                        *report.failure_report.unique_failure_messages,
                        UniqueFailureMessage(
                            failure_id="f2",
                            message="HTTP 400: region is unsupported",
                            case_ids=["case_1"],
                        ),
                    ]
                }
            )
        }
    )
    second = ready_decision()
    second["cause"] = "The region value is unsupported."
    second["solutions"] = [
        {
            "input": "query.region",
            "desired_behavior": "Use a supported region.",
            "candidate_values": ["us-east"],
        }
    ]
    second["evidence_refs"] = ["F2", "C1"]
    client = StubClient(
        [llm_response(ready_decision()), llm_response(second)]
    )

    result = OperationSmokeDiagnoser(
        client=client,
        planning_model=planning_model(),
    ).diagnose(report=report, config=smoke_config())

    assert [item.failure_ref for item in result.investigations] == ["F1", "F2"]
    assert '"failure_ref": "F1"' in client.requests[0].messages[1].content
    assert '"failure_ref": "F2"' in client.requests[1].messages[1].content


def test_probe_failure_is_deduplicated_and_inherits_root_provenance() -> None:
    """Scenario: verify that probe failure is deduplicated and inherits root provenance."""
    from restscope.agent.operation_smoke import OperationSmokeDiagnoser
    from restscope.llm import ToolCall, ToolResult

    class FailingProbe(Probe):
        def execute(self, *, config, tool_call):
            self.calls.append(tool_call)
            return ToolResult(
                tool_call_id=tool_call.id,
                name=tool_call.name,
                status="succeeded",
                structured={
                    "status_code": 400,
                    "reason_phrase": "Bad Request",
                    "body_format": "json",
                    "body": {"message": "region is required"},
                },
            )

    probe = FailingProbe()
    confirmed = confirmed_decision()
    confirmed["evidence_refs"] = ["F1", "C1", "O1", "O2"]
    discovered = {
        "action": "ready",
        "cause": "A region is required after the project is accepted.",
        "solutions": [
            {
                "input": "query.region",
                "desired_behavior": "Always send a supported region.",
                "candidate_values": ["us-east"],
            }
        ],
        "evidence_refs": ["F2", "O1", "O2"],
    }
    client = StubClient(
        [
            llm_response(hypothesis_decision()),
            llm_response(
                tool_calls=[
                    ToolCall(
                        id="probe-1",
                        name="restscope.http.request",
                        arguments={
                            "method": "GET",
                            "path": "/projects/known-1",
                        },
                    ),
                    ToolCall(
                        id="probe-2",
                        name="restscope.http.request",
                        arguments={
                            "method": "GET",
                            "path": "/projects/known-2",
                        },
                    ),
                ]
            ),
            llm_response(confirmed),
            llm_response(discovered),
        ]
    )

    result = OperationSmokeDiagnoser(
        client=client,
        planning_model=planning_model(),
        http_probe=probe,
    ).diagnose(report=smoke_report(), config=smoke_config())

    assert [item.failure_ref for item in result.investigations] == ["F1", "F2"]
    discovered_action = result.actionable_failures[1]
    assert discovered_action.failure_ref == "F2"
    assert discovered_action.root_failure_refs == ["F1"]
    assert discovered_action.evidence_origin == "probe"


def test_diagnosis_queue_processes_at_most_ten_unique_failures() -> None:
    """Scenario: verify that diagnosis queue processes at most ten unique failures."""
    from restscope.agent.operation_smoke import OperationSmokeDiagnoser
    from restscope.testing import UniqueFailureMessage

    report = smoke_report()
    failures = [
        UniqueFailureMessage(
            failure_id=f"f{index}",
            message=f"HTTP 400: failure {index}",
            case_ids=["case_1"],
        )
        for index in range(1, 12)
    ]
    report = report.model_copy(
        update={
            "failure_report": report.failure_report.model_copy(
                update={"unique_failure_messages": failures}
            )
        }
    )
    client = StubClient(
        [
            llm_response(
                {
                    "action": "deferred",
                    "reason": "insufficient_evidence",
                }
            )
            for _ in range(10)
        ]
    )

    result = OperationSmokeDiagnoser(
        client=client,
        planning_model=planning_model(),
    ).diagnose(report=report, config=smoke_config())

    assert len(result.investigations) == 10
    assert result.truncated_failure_refs == ["F11"]
    assert len(client.requests) == 10


def test_valid_output_limit_is_per_failure() -> None:
    """Scenario: verify that valid output limit is per failure."""
    from restscope.agent.operation_smoke import OperationSmokeDiagnoser

    decisions = []
    for index in range(20):
        decision = hypothesis_decision()
        decision["proposed_changes"] = [f"Try candidate value {index}."]
        decision["expected_outcome"] = f"Candidate {index} changes the response."
        decisions.append(llm_response(decision))
    client = StubClient(decisions)

    result = OperationSmokeDiagnoser(
        client=client,
        planning_model=planning_model(),
    ).diagnose(
        report=smoke_report(),
        config=smoke_config(),
        max_diagnosis_outputs_per_failure=20,
    )

    assert result.status == "inconclusive"
    assert result.valid_outputs == 20
    assert result.deferred_failures[0].reason == "output_limit"


def test_third_identical_material_hypothesis_defers_as_stalled() -> None:
    """Scenario: verify that third identical material hypothesis defers as stalled."""
    from restscope.agent.operation_smoke import OperationSmokeDiagnoser

    client = StubClient(
        [llm_response(hypothesis_decision()) for _ in range(3)]
    )

    result = OperationSmokeDiagnoser(
        client=client,
        planning_model=planning_model(),
    ).diagnose(report=smoke_report(), config=smoke_config())

    assert result.status == "inconclusive"
    assert result.valid_outputs == 1
    assert result.invalid_outputs == 2
    assert result.investigations[0].hypothesis_count == 1
    assert result.deferred_failures[0].reason == "stalled_hypothesis"


def test_three_consecutive_invalid_outputs_defer_without_using_valid_budget() -> None:
    """Scenario: verify that three consecutive invalid outputs defer without using valid budget."""
    from restscope.agent.operation_smoke import OperationSmokeDiagnoser

    client = StubClient(
        [llm_response({"action": "ready"}) for _ in range(3)]
    )

    result = OperationSmokeDiagnoser(
        client=client,
        planning_model=planning_model(),
    ).diagnose(report=smoke_report(), config=smoke_config())

    assert result.status == "inconclusive"
    assert result.valid_outputs == 0
    assert result.invalid_outputs == 3
    assert result.deferred_failures[0].reason == "invalid_output_limit"


def test_patch_grouping_is_deterministic_and_uses_no_llm() -> None:
    """Scenario: verify that patch grouping is deterministic and uses no llm."""
    from restscope.agent.operation_smoke import (
        ActionableFailure,
        ParameterSolution,
        PatchGroupPlanner,
    )

    actionable = ActionableFailure(
        item_id="I1",
        failure_ref="F1",
        root_failure_refs=["F1"],
        evidence_origin="initial",
        cause="The project identifier does not exist.",
        solutions=[
            ParameterSolution(
                input="path.projectId",
                desired_behavior="Use an identifier accepted by the API.",
                candidate_values=["known-project"],
            )
        ],
        affected_inputs=["path.projectId"],
        evidence_refs=["F1", "C1"],
    )
    result = PatchGroupPlanner().group(
        actionable_failures=[actionable],
        config=smoke_config(),
    )

    assert result.status == "grouped"
    assert len(result.tasks) == 1
    task = result.tasks[0]
    assert task.group_id == "G1"
    assert task.inputs == ["path.projectId"]
    assert task.root_failure_refs == ["F1"]
    assert task.requirements == [
        "path.projectId: Use an identifier accepted by the API."
    ]
    assert task.candidate_hints == ["known-project"]


def test_patch_grouping_splits_independent_inputs_for_one_failure() -> None:
    """Scenario: verify that patch grouping splits independent inputs for one failure."""
    from restscope.agent.operation_smoke import (
        ActionableFailure,
        ParameterSolution,
        PatchGroupPlanner,
    )

    actionable = ActionableFailure(
        item_id="I1",
        failure_ref="F1",
        root_failure_refs=["F1"],
        evidence_origin="initial",
        cause="Both generated inputs are rejected independently.",
        solutions=[
            ParameterSolution(
                input="path.projectId",
                desired_behavior="Use an identifier accepted by the API.",
            ),
            ParameterSolution(
                input="query.region",
                desired_behavior="Use an accepted region.",
            ),
        ],
        affected_inputs=["path.projectId", "query.region"],
        evidence_refs=["F1", "C1"],
    )
    result = PatchGroupPlanner().group(
        actionable_failures=[actionable],
        config=smoke_config(),
    )

    assert result.status == "grouped"
    assert [task.group_id for task in result.tasks] == ["G1", "G2"]
    assert [task.inputs for task in result.tasks] == [
        ["path.projectId"],
        ["query.region"],
    ]
    assert [task.item_ids for task in result.tasks] == [["I1"], ["I1"]]


def test_patch_grouping_merges_shared_inputs_and_collects_all_items() -> None:
    """Scenario: verify that patch grouping merges shared inputs and collects all items."""
    from restscope.agent.operation_smoke import (
        ActionableFailure,
        ParameterSolution,
        PatchGroupPlanner,
    )

    def actionable(item_id, failure_ref, behavior):
        return ActionableFailure(
            item_id=item_id,
            failure_ref=failure_ref,
            root_failure_refs=[failure_ref],
            evidence_origin="initial",
            cause=behavior,
            solutions=[
                ParameterSolution(
                    input="path.projectId",
                    desired_behavior=behavior,
                )
            ],
            affected_inputs=["path.projectId"],
            evidence_refs=[failure_ref, "C1"],
        )

    result = PatchGroupPlanner().group(
        actionable_failures=[
            actionable("I1", "F1", "Use an accepted project."),
            actionable("I2", "F2", "Use a visible project."),
        ],
        config=smoke_config(),
    )

    assert result.status == "grouped"
    assert len(result.tasks) == 1
    assert result.tasks[0].inputs == ["path.projectId"]
    assert result.tasks[0].item_ids == ["I1", "I2"]
    assert result.tasks[0].root_failure_refs == ["F1", "F2"]


def test_patch_grouping_keeps_constraint_linked_inputs_in_one_group() -> None:
    """Scenario: verify that patch grouping keeps constraint linked inputs in one group."""
    from restscope.agent.operation_smoke import (
        ActionableFailure,
        ParameterSolution,
        PatchGroupPlanner,
    )

    actionable = ActionableFailure(
        item_id="I1",
        failure_ref="F1",
        root_failure_refs=["F1"],
        evidence_origin="initial",
        cause="The project and region must describe the same deployment.",
        solutions=[
            ParameterSolution(
                input="path.projectId",
                desired_behavior="Use an accepted project.",
            ),
            ParameterSolution(
                input="query.region",
                desired_behavior="Use the region belonging to that project.",
            ),
        ],
        affected_inputs=["path.projectId", "query.region"],
        evidence_refs=["F1", "C1"],
        interaction_notes=[
            "path.projectId and query.region require one same-request constraint."
        ],
    )
    result = PatchGroupPlanner().group(
        actionable_failures=[actionable],
        config=smoke_config(),
    )

    assert result.status == "grouped"
    assert result.tasks[0].inputs == [
        "path.projectId",
        "query.region",
    ]


def test_effect_validation_accepts_group_from_initial_failure_only() -> None:
    """Scenario: verify that effect validation accepts group from initial failure only."""
    from restscope.agent.operation_smoke import (
        ActionableFailure,
        OperationSmokeDiagnoser,
        ParameterSolution,
        PlanSolveDiagnosisResult,
    )
    from restscope.agent.parameter_patch import (
        GeneratorPatchAttribution,
        GeneratorPatchDraft,
        ValidatedPatchGroup,
    )
    from restscope.testing import InputGeneratorPatch, UniqueFailureMessage

    actionable = ActionableFailure(
        item_id="I1",
        failure_ref="F1",
        root_failure_refs=["F1"],
        evidence_origin="initial",
        cause="The generated project identifier does not exist.",
        solutions=[
            ParameterSolution(
                input="path.projectId",
                desired_behavior="Use an identifier accepted by the API.",
            )
        ],
        affected_inputs=["path.projectId"],
        evidence_refs=["F1", "C1"],
    )
    diagnosis = PlanSolveDiagnosisResult(
        status="actionable",
        termination_reason="all_failures_processed",
        actionable_failures=[actionable],
    )
    group = ValidatedPatchGroup(
        group_id="G1",
        item_ids=["I1"],
        root_failure_refs=["F1"],
        patch=GeneratorPatchDraft(
            updates=[
                InputGeneratorPatch(
                    input_node_id="path/projectId",
                    strategy={"type": "constant", "value": "known-project"},
                )
            ],
            attributions=[
                GeneratorPatchAttribution(
                    input_node_id="path/projectId",
                    group_ids=["G1"],
                    item_ids=["I1"],
                    root_failure_refs=["F1"],
                )
            ],
        ),
        samples=[
            {"path.projectId": "known-project"} for _ in range(10)
        ],
        attempts=2,
    )
    baseline = smoke_report()
    baseline = baseline.model_copy(
        update={
            "failure_report": baseline.failure_report.model_copy(
                update={
                    "unique_failure_messages": [
                        *baseline.failure_report.unique_failure_messages,
                        UniqueFailureMessage(
                            failure_id="f2",
                            message="HTTP 400: Region is required",
                            case_ids=["case_1"],
                        ),
                    ]
                }
            )
        }
    )
    candidate = baseline.model_copy(
        update={
            "run_id": "run_candidate",
            "status_code_counts": {"200": 1},
            "observed_2xx": True,
            "failure_report": baseline.failure_report.model_copy(
                update={
                    "unique_failure_messages": (
                        baseline.failure_report.unique_failure_messages[1:]
                    )
                }
            ),
        }
    )
    valid_effect_decision = {
        "items": [
            {
                "item_id": "F1",
                "status": "resolved",
                "current_failure_refs": [],
                "reason": "The initial failure no longer occurs.",
                "confidence": 0.95,
            },
            {
                "item_id": "F2",
                "status": "persisting",
                "current_failure_refs": ["CF1"],
                "reason": "The unrelated initial failure remains.",
                "confidence": 0.9,
            },
        ]
    }
    client = StubClient(
        [
            llm_response({"F1": "resolved", "F2": "persisting"}),
            llm_response(valid_effect_decision),
        ]
    )
    from restscope.observability import TracingRuntime
    from restscope.redaction import Redactor

    redactor = Redactor(["runtime-secret"])

    summary = OperationSmokeDiagnoser(
        client=client,
        planning_model=planning_model(),
        tracing_runtime=TracingRuntime.disabled(redactor=redactor),
    ).validate_effect(
        baseline_report=baseline,
        candidate_report=candidate,
        baseline_private_case_evidence={
            "case_1": {
                "response_body": (
                    b'{"message":"projectId must be numeric",'
                    b'"token":"runtime-secret"}'
                ),
                "response_body_truncated": False,
                "response_encoding": "utf-8",
            }
        },
        candidate_private_case_evidence={
            "case_1": {
                "response_body": (
                    b'{"message":"region is required",'
                    b'"token":"runtime-secret"}'
                ),
                "response_body_truncated": False,
                "response_encoding": "utf-8",
            }
        },
        diagnosis=diagnosis,
        groups=[group],
    )

    assert summary.accepted_group_ids == ["G1"]
    assert summary.rejected_group_ids == []
    assert summary.accepted_input_node_ids == ["path/projectId"]
    assert [
        (item.item_id, item.status) for item in summary.items
    ] == [("F1", "resolved"), ("F2", "persisting")]
    rendered_prompt = str(client.requests[0].messages)
    assert "known-project" not in rendered_prompt
    assert '"samples":' not in rendered_prompt
    assert "input_node_id" not in rendered_prompt
    assert "Authorization" not in rendered_prompt
    assert "runtime-secret" not in rendered_prompt
    assert "***REDACTED***" in rendered_prompt
    assert '"items":[' in rendered_prompt
    payload = json.loads(client.requests[0].messages[1].content)
    baseline_response = payload["baseline"]["cases"][0]["response"]
    candidate_response = payload["candidate"]["cases"][0]["response"]
    assert baseline_response["body"] == {
        "message": "projectId must be numeric",
        "token": "***REDACTED***",
    }
    assert candidate_response["body"] == {
        "message": "region is required",
        "token": "***REDACTED***",
    }
    assert baseline_response["body_truncated"] is False
    assert candidate_response["body_truncated"] is False
    assert baseline_response["body_original_size_bytes"] > 0
    assert candidate_response["body_original_size_bytes"] > 0
    system_prompt = client.requests[0].messages[0].content
    assert "same HTTP status code alone" in system_prompt
    assert "different parameter error" in system_prompt
    repair_prompt = client.requests[1].messages[-1].content
    assert "items: Field required" in repair_prompt
    assert "The only legal top-level shape" in repair_prompt
    assert "Never return initial failure refs (F1, F2)" in repair_prompt
    assert client.requests[0].metadata["role"] == (
        "operation_smoke_effect_validation"
    )


def test_effect_validation_omits_2xx_body_and_bounds_failure_evidence() -> None:
    """Only bounded, redacted non-2xx bodies may enter effect evidence."""
    from restscope.agent.operation_smoke import (
        ActionableFailure,
        OperationSmokeDiagnoser,
        ParameterSolution,
        PlanSolveDiagnosisResult,
    )
    from restscope.agent.parameter_patch import (
        GeneratorPatchAttribution,
        GeneratorPatchDraft,
        ValidatedPatchGroup,
    )
    from restscope.observability import TracingRuntime
    from restscope.redaction import Redactor
    from restscope.testing import InputGeneratorPatch

    baseline = smoke_report()
    baseline_case = baseline.cases[0]
    candidate_case = baseline_case.model_copy(
        update={
            "case_id": "case_2",
            "response": baseline_case.response.model_copy(
                update={
                    "status_code": 200,
                    "reason_phrase": "OK",
                }
            ),
        }
    )
    candidate = baseline.model_copy(
        update={
            "run_id": "run_candidate",
            "cases": [candidate_case],
            "status_code_counts": {"200": 1},
            "observed_2xx": True,
            "failure_report": baseline.failure_report.model_copy(
                update={"unique_failure_messages": []}
            ),
        }
    )
    actionable = ActionableFailure(
        item_id="I1",
        failure_ref="F1",
        root_failure_refs=["F1"],
        evidence_origin="initial",
        cause="The generated project identifier is invalid.",
        solutions=[
            ParameterSolution(
                input="path.projectId",
                desired_behavior="Generate a numeric project identifier.",
            )
        ],
        affected_inputs=["path.projectId"],
        evidence_refs=["F1", "C1"],
    )
    diagnosis = PlanSolveDiagnosisResult(
        status="actionable",
        termination_reason="all_failures_processed",
        actionable_failures=[actionable],
    )
    group = ValidatedPatchGroup(
        group_id="G1",
        item_ids=["I1"],
        root_failure_refs=["F1"],
        patch=GeneratorPatchDraft(
            updates=[
                InputGeneratorPatch(
                    input_node_id="path/projectId",
                    strategy={"type": "integer_range", "minimum": 1, "maximum": 9},
                )
            ],
            attributions=[
                GeneratorPatchAttribution(
                    input_node_id="path/projectId",
                    group_ids=["G1"],
                    item_ids=["I1"],
                    root_failure_refs=["F1"],
                )
            ],
        ),
        samples=[{"path.projectId": 1} for _ in range(10)],
        attempts=2,
    )
    client = StubClient(
        [
            llm_response(
                {
                    "items": [
                        {
                            "item_id": "F1",
                            "status": "resolved",
                            "current_failure_refs": [],
                            "reason": "The original parameter error is absent.",
                            "confidence": 0.9,
                        }
                    ]
                }
            )
        ]
    )
    huge_failure_body = (
        b'{"message":"' + (b"x" * 80_000) + b' runtime-secret"}'
    )

    OperationSmokeDiagnoser(
        client=client,
        planning_model=planning_model(),
        tracing_runtime=TracingRuntime.disabled(
            redactor=Redactor(["runtime-secret"])
        ),
    ).validate_effect(
        baseline_report=baseline,
        candidate_report=candidate,
        baseline_private_case_evidence={
            "case_1": {
                "response_body": huge_failure_body,
                "response_body_truncated": False,
                "response_encoding": "utf-8",
            }
        },
        candidate_private_case_evidence={
            "case_2": {
                "response_body": b"2xx-body-must-not-be-visible",
                "response_body_truncated": False,
                "response_encoding": "utf-8",
            }
        },
        diagnosis=diagnosis,
        groups=[group],
    )

    user_prompt = client.requests[0].messages[1].content
    payload = json.loads(user_prompt)
    baseline_response = payload["baseline"]["cases"][0]["response"]
    candidate_response = payload["candidate"]["cases"][0]["response"]
    assert len(user_prompt.encode("utf-8")) <= 64 * 1024
    assert baseline_response["body_truncated"] is True
    assert baseline_response["body_original_size_bytes"] == len(huge_failure_body)
    assert baseline_response["body"]["truncated"] is True
    assert "runtime-secret" not in user_prompt
    assert "body" not in candidate_response
    assert "body_truncated" not in candidate_response
    assert "2xx-body-must-not-be-visible" not in user_prompt


def test_effect_evidence_handles_text_empty_and_invalid_json_bodies() -> None:
    """Failure evidence preserves useful bodies even when they are not valid JSON."""
    from restscope.agent.operation_smoke.evidence import (
        build_effect_validation_payload,
    )
    from restscope.redaction import Redactor

    report = smoke_report()
    original = report.cases[0]
    cases = [
        original.model_copy(
            update={
                "case_id": "case_text",
                "response": original.response.model_copy(
                    update={"media_type": "text/plain"}
                ),
            }
        ),
        original.model_copy(
            update={
                "case_id": "case_empty",
                "response": original.response.model_copy(
                    update={"status_code": 500}
                ),
            }
        ),
        original.model_copy(
            update={
                "case_id": "case_invalid_json",
                "response": original.response.model_copy(
                    update={"status_code": 422}
                ),
            }
        ),
    ]
    report = report.model_copy(
        update={
            "cases": cases,
            "status_code_counts": {"404": 1, "500": 1, "422": 1},
        }
    )
    private = {
        "case_text": {
            "response_body": b"employeeId must be numeric",
            "response_encoding": "utf-8",
        },
        "case_empty": {
            "response_body": b"",
            "response_encoding": "utf-8",
        },
        "case_invalid_json": {
            "response_body": b'{"message":\xff}',
            "response_encoding": "utf-8",
        },
    }

    payload = build_effect_validation_payload(
        baseline_report=report,
        candidate_report=report,
        baseline_private_case_evidence=private,
        candidate_private_case_evidence=private,
        baseline_failures=[],
        candidate_failures=[],
        confirmed_diagnoses=[],
        group_failure_mapping=[],
        redactor=Redactor(),
    )

    responses = {
        case["case_ref"]: case["response"]
        for case in payload["baseline"]["cases"]
    }
    assert responses["case_text"]["body"] == "employeeId must be numeric"
    assert responses["case_empty"]["body_available"] is True
    assert responses["case_empty"]["body"] == ""
    assert responses["case_empty"]["body_original_size_bytes"] == 0
    assert responses["case_invalid_json"]["body"] == '{"message":�}'


def test_effect_evidence_keeps_every_failure_case_under_combined_limit() -> None:
    """The shared budget shortens previews fairly without dropping cases."""
    from restscope.agent.operation_smoke.evidence import (
        build_effect_validation_payload,
    )
    from restscope.redaction import Redactor

    report = smoke_report()
    original = report.cases[0]
    cases = [
        original.model_copy(update={"case_id": f"case_{index}"})
        for index in range(20)
    ]
    report = report.model_copy(update={"cases": cases})
    private = {
        case.case_id: {
            "response_body": (
                f'{{"case":{index},"message":"'.encode()
                + (bytes([65 + index % 26]) * 20_000)
                + b'"}'
            ),
            "response_encoding": "utf-8",
        }
        for index, case in enumerate(cases)
    }

    payload = build_effect_validation_payload(
        baseline_report=report,
        candidate_report=report.model_copy(
            update={"run_id": "candidate_run"}
        ),
        baseline_private_case_evidence=private,
        candidate_private_case_evidence=private,
        baseline_failures=[],
        candidate_failures=[],
        confirmed_diagnoses=[],
        group_failure_mapping=[],
        redactor=Redactor(),
    )

    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    all_cases = [
        *payload["baseline"]["cases"],
        *payload["candidate"]["cases"],
    ]
    assert len(encoded) <= 64 * 1024
    assert len(all_cases) == 40
    assert {
        case["case_ref"] for case in payload["baseline"]["cases"]
    } == {f"case_{index}" for index in range(20)}
    assert all("body" in case["response"] for case in all_cases)
    assert all(case["response"]["body_truncated"] for case in all_cases)
    assert all(case["response"]["body"]["preview"] for case in all_cases)
