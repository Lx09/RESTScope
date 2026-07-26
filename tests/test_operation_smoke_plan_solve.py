"""Root-cause Plan & Solve contracts for Operation Smoke."""

from __future__ import annotations

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
    from restscope.llm import LLMModelConfig, ModelSelector

    selector = ModelSelector(
        thinking=LLMModelConfig(
            role="thinking",
            provider="stub",
            model="think-model",
        ),
        fast=LLMModelConfig(
            role="fast",
            provider="stub",
            model="fast-model",
        ),
    )

    assert selector.select(
        "operation_smoke_root_cause_diagnosis"
    ).model == "think-model"
    assert selector.select(
        "operation_smoke_patch_grouping"
    ).model == "fast-model"
    assert selector.select("parameter_patch_agent").model == "fast-model"
    assert selector.select(
        "operation_smoke_effect_validation"
    ).model == "think-model"


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
    from restscope.agent.operation_smoke.planning import (
        build_failure_decision_protocol,
    )

    protocol = build_failure_decision_protocol(
        input_handle="path.projectId",
        failure_ref="F1",
    )

    assert "confirmed" not in protocol.examples
    assert "action=confirmed is unavailable" in protocol.text


def test_failure_prompt_and_repair_share_the_complete_dto_protocol() -> None:
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


def test_disproved_hypothesis_is_replaced_before_confirmation() -> None:
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
    confirmed = confirmed_decision()
    confirmed["evidence_refs"] = ["F1", "C1", "O2"]
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
            llm_response(
                tool_calls=[
                    ToolCall(
                        id="probe-2",
                        name="restscope.http.request",
                        arguments={"method": "GET", "path": "/projects/two"},
                    )
                ]
            ),
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
    assert result.investigations[0].valid_outputs == 5
    assert result.investigations[0].http_tool_calls == 2


def test_invalid_tool_call_prevents_the_entire_tool_batch_from_executing() -> None:
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
    from restscope.agent.operation_smoke import OperationSmokeDiagnoser

    client = StubClient(
        [llm_response(hypothesis_decision()) for _ in range(20)]
    )

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


def test_three_consecutive_invalid_outputs_defer_without_using_valid_budget() -> None:
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


def test_patch_grouping_only_combines_confirmed_inputs_and_requirements() -> None:
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
    client = StubClient(
        [
            llm_response(
                {
                    "groups": [
                        {
                            "item_ids": ["I1"],
                            "inputs": ["path.projectId"],
                        }
                    ],
                    "deferred_item_ids": [],
                }
            )
        ]
    )

    result = PatchGroupPlanner(
        client=client,
        model=patch_model_for_grouping(),
    ).group(actionable_failures=[actionable], config=smoke_config())

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
    assert client.requests[0].metadata["role"] == (
        "operation_smoke_patch_grouping"
    )


def test_patch_grouping_repairs_an_invented_input_once() -> None:
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
            )
        ],
        affected_inputs=["path.projectId"],
        evidence_refs=["F1", "C1"],
    )
    client = StubClient(
        [
            llm_response(
                {
                    "groups": [
                        {
                            "item_ids": ["I1"],
                            "inputs": ["query.region"],
                        }
                    ],
                    "deferred_item_ids": [],
                }
            ),
            llm_response(
                {
                    "groups": [
                        {
                            "item_ids": ["I1"],
                            "inputs": ["path.projectId"],
                        }
                    ],
                    "deferred_item_ids": [],
                }
            ),
        ]
    )

    result = PatchGroupPlanner(
        client=client,
        model=patch_model_for_grouping(),
    ).group(actionable_failures=[actionable], config=smoke_config())

    assert result.status == "grouped"
    assert len(client.requests) == 2
    assert "query.region was not supplied" in (
        client.requests[1].messages[-1].content
    )


def test_patch_grouping_must_route_every_actionable_parameter() -> None:
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
        cause="The two parameters must be coordinated.",
        solutions=[
            ParameterSolution(
                input="path.projectId",
                desired_behavior="Use an accepted project.",
            ),
            ParameterSolution(
                input="query.region",
                desired_behavior="Use its matching region.",
            ),
        ],
        affected_inputs=["path.projectId", "query.region"],
        evidence_refs=["F1", "C1"],
    )
    client = StubClient(
        [
            llm_response(
                {
                    "groups": [
                        {
                            "item_ids": ["I1"],
                            "inputs": ["path.projectId"],
                        }
                    ],
                    "deferred_item_ids": [],
                }
            ),
            llm_response(
                {
                    "groups": [
                        {
                            "item_ids": ["I1"],
                            "inputs": [
                                "path.projectId",
                                "query.region",
                            ],
                        }
                    ],
                    "deferred_item_ids": [],
                }
            ),
        ]
    )

    result = PatchGroupPlanner(
        client=client,
        model=patch_model_for_grouping(),
    ).group(actionable_failures=[actionable], config=smoke_config())

    assert result.status == "grouped"
    assert result.tasks[0].inputs == [
        "path.projectId",
        "query.region",
    ]
    assert "query.region" in client.requests[1].messages[-1].content


def test_patch_grouping_keeps_constraint_linked_inputs_in_one_group() -> None:
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
    client = StubClient(
        [
            llm_response(
                {
                    "groups": [
                        {
                            "item_ids": ["I1"],
                            "inputs": ["path.projectId"],
                        },
                        {
                            "item_ids": ["I1"],
                            "inputs": ["query.region"],
                        },
                    ],
                    "deferred_item_ids": [],
                }
            ),
            llm_response(
                {
                    "groups": [
                        {
                            "item_ids": ["I1"],
                            "inputs": [
                                "path.projectId",
                                "query.region",
                            ],
                        }
                    ],
                    "deferred_item_ids": [],
                }
            ),
        ]
    )

    result = PatchGroupPlanner(
        client=client,
        model=patch_model_for_grouping(),
    ).group(actionable_failures=[actionable], config=smoke_config())

    assert result.status == "grouped"
    assert result.tasks[0].inputs == [
        "path.projectId",
        "query.region",
    ]
    assert "same Patch Group" in client.requests[1].messages[-1].content


def patch_model_for_grouping():
    from restscope.llm import LLMModelConfig

    return LLMModelConfig(
        role="operation_smoke_patch_grouping",
        provider="stub",
        model="fast-model",
        reasoning={"mode": "disabled"},
    )


def test_effect_validation_accepts_group_from_initial_failure_only() -> None:
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
                update={"unique_failure_messages": []}
            ),
        }
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
            )
        ]
    )

    summary = OperationSmokeDiagnoser(
        client=client,
        planning_model=planning_model(),
    ).validate_effect(
        baseline_report=baseline,
        candidate_report=candidate,
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
    assert client.requests[0].metadata["role"] == (
        "operation_smoke_effect_validation"
    )
