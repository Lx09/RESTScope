from __future__ import annotations

import pytest
from pydantic import ValidationError

from tests._operation_smoke_plan_solve_fixtures import (
    smoke_config,
    smoke_report,
)


def test_smoke_request_exposes_lowerable_plan_and_tool_budgets() -> None:
    from restscope.agent.operation_smoke import OperationSmokeRequest

    request = OperationSmokeRequest(operation_key="GET /items")

    assert request.max_planning_outputs == 20
    assert request.max_http_tool_rounds == 40
    assert (
        OperationSmokeRequest(
            operation_key="GET /items",
            max_planning_outputs=3,
            max_http_tool_rounds=0,
        ).model_dump()
        | {}
    )["max_planning_outputs"] == 3

    with pytest.raises(ValidationError):
        OperationSmokeRequest(
            operation_key="GET /items",
            max_planning_outputs=21,
        )
    with pytest.raises(ValidationError):
        OperationSmokeRequest(
            operation_key="GET /items",
            max_http_tool_rounds=41,
        )


def test_plan_solve_result_replaces_two_round_public_result() -> None:
    import restscope.agent.operation_smoke as smoke

    result = smoke.PlanSolveDiagnosisResult(
        status="inconclusive",
        termination_reason="decision_limit",
        planning_outputs=20,
        http_tool_rounds=4,
    )

    assert result.patch is None
    assert result.ready_items == []
    assert result.pending_items == []
    assert not hasattr(smoke, "TwoRoundDiagnosisResult")


def test_semantic_input_handles_hide_node_ids_and_active_media_type() -> None:
    from restscope.agent.operation_smoke.evidence import build_semantic_input_map
    from restscope.testing import (
        InputGeneratorConfig,
        InputNodeSnapshot,
        OperationGeneratorConfig,
        OperationTestSnapshot,
        SchemaSnapshot,
    )

    config = OperationGeneratorConfig(
        operation_key="POST /orders/{orderId}",
        revision=1,
        snapshot=OperationTestSnapshot(
            operation_key="POST /orders/{orderId}",
            method="POST",
            path="/orders/{orderId}",
            parameters=[],
            request_body_node_id="node_request",
            media_type_node_ids={"application/json": "node_body"},
            available_media_types=["application/json"],
            input_nodes=[
                InputNodeSnapshot(
                    input_node_id="node_path",
                    node_kind="parameter",
                    canonical_path="path/orderId",
                    required=True,
                    schema_contract=SchemaSnapshot(type="string"),
                ),
                InputNodeSnapshot(
                    input_node_id="node_request",
                    node_kind="request_body",
                    canonical_path="body",
                    required=True,
                ),
                InputNodeSnapshot(
                    input_node_id="node_body",
                    node_kind="schema",
                    canonical_path="body/application~1json",
                    parent_node_id="node_request",
                    required=True,
                    schema_contract=SchemaSnapshot(type="object"),
                ),
                InputNodeSnapshot(
                    input_node_id="node_items",
                    node_kind="schema",
                    canonical_path=(
                        "body/application~1json/properties/items"
                    ),
                    parent_node_id="node_body",
                    required=True,
                    schema_contract=SchemaSnapshot(type="array"),
                ),
                InputNodeSnapshot(
                    input_node_id="node_item",
                    node_kind="schema",
                    canonical_path=(
                        "body/application~1json/properties/items/items"
                    ),
                    parent_node_id="node_items",
                    required=True,
                    schema_contract=SchemaSnapshot(type="object"),
                ),
                InputNodeSnapshot(
                    input_node_id="node_sku",
                    node_kind="schema",
                    canonical_path=(
                        "body/application~1json/properties/items/items/"
                        "properties/sku"
                    ),
                    parent_node_id="node_item",
                    required=True,
                    schema_contract=SchemaSnapshot(type="string"),
                ),
            ],
        ),
        active_media_type="application/json",
        configs=[
            InputGeneratorConfig(
                input_node_id="node_path",
                inclusion_probability=1,
                strategy={"type": "random_string"},
            ),
            InputGeneratorConfig(
                input_node_id="node_request",
                inclusion_probability=1,
                strategy={"type": "request_body"},
            ),
            InputGeneratorConfig(
                input_node_id="node_body",
                inclusion_probability=1,
                strategy={"type": "object"},
            ),
            InputGeneratorConfig(
                input_node_id="node_items",
                inclusion_probability=1,
                strategy={"type": "array"},
            ),
            InputGeneratorConfig(
                input_node_id="node_item",
                inclusion_probability=1,
                strategy={"type": "object"},
            ),
            InputGeneratorConfig(
                input_node_id="node_sku",
                inclusion_probability=1,
                strategy={"type": "random_string"},
            ),
        ],
    )

    semantic = build_semantic_input_map(config)

    assert semantic.handle_by_node["node_path"] == "path.orderId"
    assert semantic.handle_by_node["node_body"] == "body"
    assert semantic.handle_by_node["node_items"] == "body.items"
    assert semantic.handle_by_node["node_item"] == "body.items[]"
    assert semantic.handle_by_node["node_sku"] == "body.items[].sku"
    assert semantic.node_by_handle["body.items[].sku"] == "node_sku"
    assert "node_request" not in semantic.handle_by_node
    assert all("application" not in handle for handle in semantic.node_by_handle)


def test_plan_solve_uses_thinking_model_and_patch_stays_fast() -> None:
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

    assert (
        selector.select("operation_smoke_plan_solve").model
        == "think-model"
    )
    assert (
        selector.select("operation_smoke_generator_patch").model
        == "fast-model"
    )


def test_initial_plan_prompt_uses_semantic_handles_and_batch_evidence() -> None:
    from restscope.agent.operation_smoke.evidence import EvidenceJournal
    from restscope.agent.operation_smoke.prompts import build_plan_prompt
    config = smoke_config()
    journal = EvidenceJournal.from_batch(
        report=smoke_report(),
        config=config,
    )

    prompt = build_plan_prompt(
        config=config,
        journal=journal,
        plan_state=None,
        previous_experiment=None,
    )

    assert "GET /projects/{projectId}" in prompt.user
    assert "path.projectId" in prompt.user
    assert "query.region" in prompt.user
    assert '"random-123"' in prompt.user
    assert "HTTP 404: Project not found" in prompt.user
    assert '"status_code_counts"' in prompt.user
    assert '"404": 1' in prompt.user
    assert '"behavior_monitor_warning_count"' in prompt.user
    assert "F1" in prompt.user
    assert "C1" in prompt.user
    assert "path/projectId" not in prompt.user
    assert "query/region" not in prompt.user
    assert "input_node_id" not in prompt.user
    assert "config_revision" not in prompt.user
    assert "must-not-enter-the-prompt" not in prompt.user


def test_plan_state_assigns_item_ids_and_supports_ready_pending_transitions() -> None:
    from restscope.agent.operation_smoke.evidence import EvidenceJournal
    from restscope.agent.operation_smoke.planning import (
        ParameterSolutionDecision,
        PlanDecision,
        PlanState,
        ReadyPlanDecision,
        PendingPlanDecision,
    )
    journal = EvidenceJournal.from_batch(
        report=smoke_report(),
        config=smoke_config(),
    )
    state, errors = PlanState.from_decision(
        PlanDecision(
            ready=[
                ReadyPlanDecision(
                    failure_refs=["F1"],
                    cause="The generated project identifier does not exist.",
                    confidence=0.9,
                    solutions=[
                        ParameterSolutionDecision(
                            input="path.projectId",
                            desired_behavior="Use an observed project identifier.",
                        )
                    ],
                    evidence_refs=["F1", "C1"],
                )
            ],
            pending=[],
            non_parameter_failure_refs=[],
            unplanned_failure_refs=[],
            finish=False,
        ),
        journal=journal,
        previous=None,
    )

    assert errors == []
    assert state is not None
    assert state.ready[0].item_id == "I1"

    updated, errors = PlanState.from_decision(
        PlanDecision(
            ready=[],
            pending=[
                PendingPlanDecision(
                    item_id="I1",
                    failure_refs=["F1"],
                    hypothesis="The ID may need to come from an existing project.",
                    missing_evidence="A request using a known identifier.",
                    next_probe="Retry the current operation with a known ID.",
                    evidence_refs=["F1", "C1"],
                )
            ],
            non_parameter_failure_refs=[],
            unplanned_failure_refs=[],
            finish=False,
        ),
        journal=journal,
        previous=state,
    )

    assert errors == []
    assert updated is not None
    assert updated.ready == []
    assert updated.pending[0].item_id == "I1"


def test_plan_state_rejects_unknown_inputs_evidence_and_disappearing_failures() -> None:
    from restscope.agent.operation_smoke.evidence import EvidenceJournal
    from restscope.agent.operation_smoke.planning import (
        ParameterSolutionDecision,
        PlanDecision,
        PlanState,
        ReadyPlanDecision,
    )
    journal = EvidenceJournal.from_batch(
        report=smoke_report(),
        config=smoke_config(),
    )
    state, errors = PlanState.from_decision(
        PlanDecision(
            ready=[
                ReadyPlanDecision(
                    failure_refs=["F9"],
                    cause="Guess",
                    confidence=0.1,
                    solutions=[
                        ParameterSolutionDecision(
                            input="path.forged",
                            desired_behavior="Guess",
                        )
                    ],
                    evidence_refs=["O9"],
                )
            ],
            pending=[],
            non_parameter_failure_refs=[],
            unplanned_failure_refs=[],
            finish=False,
        ),
        journal=journal,
        previous=None,
    )

    assert state is None
    assert "F9 was not supplied as a failure." in errors
    assert "path.forged was not offered as an input." in errors
    assert "O9 was not supplied as evidence." in errors
    assert "F1 must be classified exactly once." in errors


def test_current_operation_probe_constrains_spec_and_rejects_scope_before_network() -> None:
    import httpx

    from restscope.agent.operation_smoke.probe import CurrentOperationHTTPProbe
    from restscope.capabilities import ToolContext, build_capabilities
    from restscope.http_transport import TargetHTTPTransport
    from restscope.llm import ToolCall
    from restscope.openapi_parser import OpenAPIParser
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            404,
            headers={"Content-Type": "application/json"},
            json={"message": "not found"},
        )

    transport = TargetHTTPTransport(
        client_factory=lambda **kwargs: httpx.Client(
            transport=httpx.MockTransport(respond),
            **kwargs,
        )
    )
    runtime = build_capabilities(target_http_transport=transport)
    ir = OpenAPIParser.parse(
        {
            "openapi": "3.0.3",
            "info": {"title": "probe", "version": "1"},
            "paths": {
                "/projects/{projectId}": {
                    "get": {"responses": {"200": {"description": "ok"}}}
                }
            },
        }
    )
    runtime.tool_executor.bind_context(
        ToolContext(
            ir=ir,
            baseline_schema_source={
                "kind": "inline",
                "format": "json",
                "content": "{}",
            },
            base_url="https://api.example.test",
            headers={"Authorization": "Bearer target-secret"},
        )
    )
    probe = CurrentOperationHTTPProbe(runtime.tool_executor)
    config = smoke_config()

    spec = probe.tool_spec(config)
    assert spec.name == "restscope.http.request"
    assert spec.input_schema["properties"]["method"]["enum"] == ["GET"]
    assert "/projects/{projectId}" in spec.description

    wrong_method = probe.execute(
        config=config,
        tool_call=ToolCall(
            id="wrong-method",
            name="restscope.http.request",
            arguments={"method": "POST", "path": "/projects/known"},
        ),
    )
    wrong_path = probe.execute(
        config=config,
        tool_call=ToolCall(
            id="wrong-path",
            name="restscope.http.request",
            arguments={"method": "GET", "path": "/users/known"},
        ),
    )
    encoded_path_escape = probe.execute(
        config=config,
        tool_call=ToolCall(
            id="encoded-path-escape",
            name="restscope.http.request",
            arguments={
                "method": "GET",
                "path": "/projects/known%2Fother",
            },
        ),
    )

    assert wrong_method.status == "denied"
    assert wrong_path.status == "denied"
    assert encoded_path_escape.status == "denied"
    assert requests == []

    accepted = probe.execute(
        config=config,
        tool_call=ToolCall(
            id="accepted",
            name="restscope.http.request",
            arguments={"method": "GET", "path": "/projects/known"},
        ),
    )

    assert accepted.status == "succeeded"
    assert accepted.structured["status_code"] == 404
    assert len(requests) == 1
    assert requests[0].headers["Authorization"] == "Bearer target-secret"


def test_http_tool_public_result_does_not_expose_private_processor_details() -> None:
    import httpx

    from restscope.capabilities import ToolContext, build_capabilities
    from restscope.http_transport import (
        TargetHTTPTransport,
        TargetResponseProcessorResult,
    )
    from restscope.llm import ToolCall
    from restscope.openapi_parser import OpenAPIParser

    class Processor:
        def process(self, observation, context):
            return TargetResponseProcessorResult(
                response_validation="evaluated",
                details={"contract_status": "updated"},
            )

    runtime = build_capabilities(
        target_http_transport=TargetHTTPTransport(
            client_factory=lambda **kwargs: httpx.Client(
                transport=httpx.MockTransport(
                    lambda request: httpx.Response(
                        200,
                        headers={"Content-Type": "application/json"},
                        json={"id": 7},
                    )
                ),
                **kwargs,
            ),
            response_processor=Processor(),
        )
    )
    ir = OpenAPIParser.parse(
        {
            "openapi": "3.0.3",
            "info": {"title": "details", "version": "1"},
            "paths": {
                "/items": {
                    "get": {"responses": {"200": {"description": "ok"}}}
                }
            },
        }
    )
    runtime.tool_executor.bind_context(
        ToolContext(
            ir=ir,
            baseline_schema_source={
                "kind": "inline",
                "format": "json",
                "content": "{}",
            },
            base_url="https://api.example.test",
        )
    )

    result = runtime.tool_executor.execute(
        tool_call=ToolCall(
            id="request",
            name="restscope.http.request",
            arguments={"method": "GET", "path": "/items"},
        ),
        role="operation_smoke_plan_solve",
        state={},
    )

    assert "behavior_monitor" not in result.structured
    assert "response_processor_details" not in result.metadata


class _StubClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def invoke(self, request):
        self.requests.append(request)
        return self.responses.pop(0)


def _llm_response(payload=None, *, tool_calls=None, content=None):
    from restscope.llm import LLMResponse

    return LLMResponse(
        provider="stub",
        model="model",
        parsed_json=payload,
        content=content,
        tool_calls=tool_calls or [],
    )


def _planning_model():
    from restscope.llm import LLMModelConfig

    return LLMModelConfig(
        role="operation_smoke_plan_solve",
        provider="stub",
        model="think-model",
        reasoning={"mode": "enabled", "effort": "high"},
    )


def _patch_model():
    from restscope.llm import LLMModelConfig

    return LLMModelConfig(
        role="operation_smoke_generator_patch",
        provider="stub",
        model="fast-model",
        reasoning={"mode": "disabled"},
    )


def _ready_plan(*, failure_refs=None, finish=True):
    return {
        "ready": [
            {
                "failure_refs": failure_refs or ["F1"],
                "cause": "The generated project identifier does not exist.",
                "confidence": 0.95,
                "solutions": [
                    {
                        "input": "path.projectId",
                        "desired_behavior": (
                            "Use a project identifier accepted by the API."
                        ),
                        "candidate_values": ["known-project"],
                    }
                ],
                "evidence_refs": ["F1", "C1"],
                "interaction_notes": [],
            }
        ],
        "pending": [],
        "non_parameter_failure_refs": [],
        "unplanned_failure_refs": [],
        "finish": finish,
    }


def _joint_patch():
    return {
        "covered_item_ids": ["I1"],
        "deferred_items": [],
        "changes": [
            {
                "input": "path.projectId",
                "generation": {
                    "kind": "sample_values",
                    "values": ["known-project"],
                },
            }
        ],
    }


def test_diagnoser_uses_think_plan_then_one_fast_joint_patch() -> None:
    from restscope.agent.operation_smoke import OperationSmokeDiagnoser
    client = _StubClient(
        [
            _llm_response(_ready_plan()),
            _llm_response(_joint_patch()),
        ]
    )

    result = OperationSmokeDiagnoser(
        client=client,
        planning_model=_planning_model(),
        patch_model=_patch_model(),
    ).diagnose(
        report=smoke_report(),
        config=smoke_config(),
    )

    assert result.status == "patch_ready"
    assert result.termination_reason == "model_finalize"
    assert result.planning_outputs == 1
    assert result.http_tool_rounds == 0
    assert result.ready_items[0].item_id == "I1"
    assert result.covered_item_ids == ["I1"]
    assert result.patch.updates[0].input_node_id == "path/projectId"
    assert result.patch.updates[0].strategy.type == "choice"
    assert [request.model for request in client.requests] == [
        "think-model",
        "fast-model",
    ]
    assert client.requests[0].tool_choice == "none"
    assert client.requests[1].tool_choice == "none"
    assert all(request.response_format == "json" for request in client.requests)


def test_pending_plan_can_probe_and_promote_new_failure_before_joint_patch() -> None:
    from restscope.agent.operation_smoke import OperationSmokeDiagnoser
    from restscope.llm import ToolCall, ToolResult, ToolSpec
    class Probe:
        def __init__(self):
            self.calls = []

        def tool_spec(self, config):
            return ToolSpec(
                name="restscope.http.request",
                description="current operation only",
                kind="local_function",
                input_schema={"type": "object"},
                risk_level="high",
                read_only=False,
            )

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
                    "body": {"message": "projectId has the wrong shape"},
                    "behavior_monitor_warnings": [],
                },
                metadata={
                    "response_processor_details": {
                        "contract_status": "updated"
                    }
                },
            )

    pending = {
        "ready": [],
        "pending": [
            {
                "failure_refs": ["F1"],
                "hypothesis": "The project identifier format is rejected.",
                "missing_evidence": "A controlled identifier request.",
                "next_probe": "Probe a known-shaped identifier.",
                "evidence_refs": ["F1", "C1"],
            }
        ],
        "non_parameter_failure_refs": [],
        "unplanned_failure_refs": [],
        "finish": False,
    }
    promoted = _ready_plan(failure_refs=["F1", "F2"])
    promoted["ready"][0]["item_id"] = "I1"
    promoted["ready"][0]["evidence_refs"] = ["F1", "C1", "O1", "F2"]
    probe = Probe()
    client = _StubClient(
        [
            _llm_response(pending),
            _llm_response(
                tool_calls=[
                    ToolCall(
                        id="probe-1",
                        name="restscope.http.request",
                        arguments={
                            "method": "GET",
                            "path": "/projects/known-shape",
                        },
                    )
                ]
            ),
            _llm_response(promoted),
            _llm_response(_joint_patch()),
        ]
    )

    result = OperationSmokeDiagnoser(
        client=client,
        planning_model=_planning_model(),
        patch_model=_patch_model(),
        http_probe=probe,
    ).diagnose(
        report=smoke_report(),
        config=smoke_config(),
        max_http_tool_rounds=1,
    )

    assert result.status == "patch_ready"
    assert result.planning_outputs == 2
    assert result.http_tool_rounds == 1
    assert len(probe.calls) == 1
    assert "F2" in result.ready_items[0].failure_refs
    assert client.requests[1].tool_choice == "auto"
    assert [tool.name for tool in client.requests[1].tools] == [
        "restscope.http.request"
    ]
    assert client.requests[2].tools == []


def test_invalid_plan_gets_one_free_repair_without_consuming_extra_budget() -> None:
    from restscope.agent.operation_smoke import OperationSmokeDiagnoser
    invalid = _ready_plan()
    invalid["ready"][0]["solutions"][0]["input"] = "path.forged"
    client = _StubClient(
        [
            _llm_response(invalid),
            _llm_response(_ready_plan()),
            _llm_response(_joint_patch()),
        ]
    )

    result = OperationSmokeDiagnoser(
        client=client,
        planning_model=_planning_model(),
        patch_model=_patch_model(),
    ).diagnose(
        report=smoke_report(),
        config=smoke_config(),
        max_planning_outputs=1,
    )

    assert result.status == "patch_ready"
    assert result.planning_outputs == 1
    assert len(client.requests) == 3
    assert "path.forged was not offered" in (
        client.requests[1].messages[-1].content
    )


def test_second_invalid_plan_output_preserves_state_and_returns_inconclusive() -> None:
    from restscope.agent.operation_smoke import OperationSmokeDiagnoser
    invalid = {
        "ready": [],
        "pending": [],
        "non_parameter_failure_refs": [],
        "unplanned_failure_refs": [],
        "finish": False,
    }
    client = _StubClient(
        [
            _llm_response(invalid),
            _llm_response(invalid),
        ]
    )

    result = OperationSmokeDiagnoser(
        client=client,
        planning_model=_planning_model(),
        patch_model=_patch_model(),
    ).diagnose(
        report=smoke_report(),
        config=smoke_config(),
    )

    assert result.status == "inconclusive"
    assert result.termination_reason == "planning_output_invalid"
    assert result.planning_outputs == 0
    assert len(client.requests) == 2


def test_all_non_parameter_failures_finish_without_fast_patch() -> None:
    from restscope.agent.operation_smoke import OperationSmokeDiagnoser

    client = _StubClient(
        [
            _llm_response(
                {
                    "ready": [],
                    "pending": [],
                    "non_parameter_failure_refs": ["F1"],
                    "unplanned_failure_refs": [],
                    "finish": True,
                }
            )
        ]
    )

    result = OperationSmokeDiagnoser(
        client=client,
        planning_model=_planning_model(),
        patch_model=_patch_model(),
    ).diagnose(report=smoke_report(), config=smoke_config())

    assert result.status == "no_parameter_issue"
    assert result.non_parameter_failures == ["F1"]
    assert result.patch is None
    assert len(client.requests) == 1


def test_decision_limit_still_patches_ready_items_and_keeps_pending_summary() -> None:
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
                            message="HTTP 400: region is not supported",
                            case_ids=["case_1"],
                        ),
                    ]
                }
            )
        }
    )
    partial = _ready_plan(failure_refs=["F1"])
    partial["ready"][0]["evidence_refs"] = ["F1", "C1"]
    partial["pending"] = [
        {
            "failure_refs": ["F2"],
            "hypothesis": "The optional region may be rejected.",
            "missing_evidence": "A request omitting region.",
            "next_probe": "Omit region and retry the operation.",
            "evidence_refs": ["F2", "C1"],
        }
    ]
    partial["finish"] = False
    client = _StubClient(
        [
            _llm_response(partial),
            _llm_response(_joint_patch()),
        ]
    )

    result = OperationSmokeDiagnoser(
        client=client,
        planning_model=_planning_model(),
        patch_model=_patch_model(),
    ).diagnose(
        report=report,
        config=smoke_config(),
        max_planning_outputs=1,
    )

    assert result.status == "patch_ready"
    assert result.termination_reason == "decision_limit"
    assert result.planning_outputs == 1
    assert [item.item_id for item in result.ready_items] == ["I1"]
    assert [item.item_id for item in result.pending_items] == ["I2"]


def test_fast_patch_repairs_accounting_once_and_all_deferred_is_inconclusive() -> None:
    from restscope.agent.operation_smoke import OperationSmokeDiagnoser

    client = _StubClient(
        [
            _llm_response(_ready_plan()),
            _llm_response(
                {
                    "covered_item_ids": [],
                    "deferred_items": [],
                    "changes": [],
                }
            ),
            _llm_response(
                {
                    "covered_item_ids": [],
                    "deferred_items": [
                        {
                            "item_id": "I1",
                            "reason": "Conflicts with the only safe change.",
                        }
                    ],
                    "changes": [],
                }
            ),
        ]
    )

    result = OperationSmokeDiagnoser(
        client=client,
        planning_model=_planning_model(),
        patch_model=_patch_model(),
    ).diagnose(report=smoke_report(), config=smoke_config())

    assert result.status == "inconclusive"
    assert result.termination_reason == "all_ready_items_deferred"
    assert result.deferred_items[0].item_id == "I1"
    assert len(client.requests) == 3
    assert "I1 must be covered or deferred exactly once" in (
        client.requests[2].messages[-1].content
    )


def test_evidence_includes_private_failure_body_and_monitor_without_target_auth() -> None:
    from restscope.agent.operation_smoke.evidence import EvidenceJournal
    from restscope.testing.execution import SmokeCaseExecutionEvidence

    journal = EvidenceJournal.from_batch(
        report=smoke_report(),
        config=smoke_config(),
        private_case_evidence={
            "case_1": SmokeCaseExecutionEvidence(
                case_id="case_1",
                response_body=b'{"detail":"unknown project"}',
                response_encoding="utf-8",
                behavior_monitor={
                    "contract_status": "mismatch",
                    "resource_status": "not_observed",
                },
            )
        },
    )

    rendered = str(journal.prompt_records())
    assert "unknown project" in rendered
    assert "contract_status" in rendered
    assert "not_observed" in rendered
    assert "must-not-enter-the-prompt" not in rendered


def test_plan_item_allocation_does_not_collide_with_reused_ids() -> None:
    from restscope.agent.operation_smoke.evidence import EvidenceJournal
    from restscope.agent.operation_smoke.planning import (
        PendingPlanDecision,
        PlanDecision,
        PlanState,
        ReadyPlanDecision,
        ParameterSolutionDecision,
    )
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
                            message="HTTP 400: another failure",
                            case_ids=["case_1"],
                        ),
                    ]
                }
            )
        }
    )
    journal = EvidenceJournal.from_batch(report=report, config=smoke_config())
    previous, errors = PlanState.from_decision(
        PlanDecision(
            ready=[],
            pending=[
                PendingPlanDecision(
                    failure_refs=["F1"],
                    hypothesis="First hypothesis.",
                    missing_evidence="First evidence.",
                    next_probe="First probe.",
                    evidence_refs=["F1", "C1"],
                ),
                PendingPlanDecision(
                    failure_refs=["F2"],
                    hypothesis="Second hypothesis.",
                    missing_evidence="Second evidence.",
                    next_probe="Second probe.",
                    evidence_refs=["F2", "C1"],
                ),
            ],
        ),
        journal=journal,
        previous=None,
    )
    assert errors == []
    assert previous is not None

    updated, errors = PlanState.from_decision(
        PlanDecision(
            ready=[
                ReadyPlanDecision(
                    failure_refs=["F1"],
                    cause="A newly split cause.",
                    confidence=0.8,
                    solutions=[
                        ParameterSolutionDecision(
                            input="path.projectId",
                            desired_behavior="Use a known ID.",
                        )
                    ],
                    evidence_refs=["F1", "C1"],
                )
            ],
            pending=[
                PendingPlanDecision(
                    item_id="I1",
                    failure_refs=["F2"],
                    hypothesis="The merged hypothesis remains.",
                    missing_evidence="One more observation.",
                    next_probe="Probe once.",
                    evidence_refs=["F2", "C1"],
                )
            ],
        ),
        journal=journal,
        previous=previous,
    )

    assert errors == []
    assert updated is not None
    assert len({updated.ready[0].item_id, updated.pending[0].item_id}) == 2


def test_previous_experiment_uses_semantic_inputs_without_revisions_or_node_ids() -> None:
    from restscope.agent.operation_smoke.agent import (
        _previous_experiment_summary,
    )
    from restscope.agent.operation_smoke.schemas import (
        GeneratorPatchDraft,
        PlanSolveDiagnosisResult,
    )

    diagnosis = PlanSolveDiagnosisResult(
        status="patch_ready",
        termination_reason="model_finalize",
        patch=GeneratorPatchDraft(
            updates=[
                {
                    "input_node_id": "path/projectId",
                    "strategy": {
                        "type": "constant",
                        "value": "known-project",
                    },
                }
            ]
        ),
        covered_item_ids=["I1"],
    )

    summary = _previous_experiment_summary(
        diagnosis,
        evaluation={"success_rate": 0.4, "case_count": 10},
        config=smoke_config(),
    )

    assert summary["generator_changes"] == [
        {
            "input": "path.projectId",
            "inclusion_probability": None,
            "generation": {
                "type": "constant",
                "value": "known-project",
            },
        }
    ]
    rendered = str(summary)
    assert "path/projectId" not in rendered
    assert "input_node_id" not in rendered
    assert "revision" not in rendered


def test_more_than_four_http_calls_are_repaired_before_any_tool_executes() -> None:
    from restscope.agent.operation_smoke import OperationSmokeDiagnoser
    from restscope.llm import ToolCall, ToolResult, ToolSpec

    class Probe:
        def __init__(self):
            self.calls = []

        def tool_spec(self, config):
            return ToolSpec(
                name="restscope.http.request",
                description="current operation only",
                kind="local_function",
                input_schema={"type": "object"},
                risk_level="high",
                read_only=False,
            )

        def execute(self, *, config, tool_call):
            self.calls.append(tool_call)
            return ToolResult(
                tool_call_id=tool_call.id,
                name=tool_call.name,
                status="succeeded",
                structured={"status_code": 200},
            )

    pending = {
        "ready": [],
        "pending": [
            {
                "failure_refs": ["F1"],
                "hypothesis": "The ID format may be wrong.",
                "missing_evidence": "A controlled request.",
                "next_probe": "Probe a known-shaped ID.",
                "evidence_refs": ["F1", "C1"],
            }
        ],
        "non_parameter_failure_refs": [],
        "unplanned_failure_refs": [],
        "finish": False,
    }
    too_many_calls = [
        ToolCall(
            id=f"call-{index}",
            name="restscope.http.request",
            arguments={
                "method": "GET",
                "path": f"/projects/candidate-{index}",
            },
        )
        for index in range(5)
    ]
    repaired = _ready_plan()
    repaired["ready"][0]["item_id"] = "I1"
    probe = Probe()
    client = _StubClient(
        [
            _llm_response(pending),
            _llm_response(tool_calls=too_many_calls),
            _llm_response(repaired),
            _llm_response(_joint_patch()),
        ]
    )

    result = OperationSmokeDiagnoser(
        client=client,
        planning_model=_planning_model(),
        patch_model=_patch_model(),
        http_probe=probe,
    ).diagnose(report=smoke_report(), config=smoke_config())

    assert result.status == "patch_ready"
    assert result.planning_outputs == 2
    assert result.http_tool_rounds == 0
    assert probe.calls == []
    assert "At most 4 HTTP requests" in client.requests[2].messages[-1].content


def test_evidence_entries_have_independent_64_kib_structured_limits() -> None:
    from restscope.agent.operation_smoke.evidence import EvidenceJournal

    journal = EvidenceJournal.from_batch(
        report=smoke_report(long_value="值" * 100_000),
        config=smoke_config(),
    )

    case = journal.entry("C1")
    assert case is not None
    assert case.truncated is True
    assert case.original_size_bytes > 64 * 1024
    assert isinstance(case.value, dict)
    assert case.value["truncated"] is True
    assert not isinstance(case.value.get("preview"), str)


def test_each_covered_item_requires_a_change_to_one_of_its_own_inputs() -> None:
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
                            message="HTTP 400: unsupported region",
                            case_ids=["case_1"],
                        ),
                    ]
                }
            )
        }
    )
    plan = _ready_plan()
    plan["ready"].append(
        {
            "failure_refs": ["F2"],
            "cause": "The region value is unsupported.",
            "confidence": 0.9,
            "solutions": [
                {
                    "input": "query.region",
                    "desired_behavior": "Use a supported region.",
                }
            ],
            "evidence_refs": ["F2", "C1"],
            "interaction_notes": [],
        }
    )
    invalid_patch = _joint_patch()
    invalid_patch["covered_item_ids"] = ["I1", "I2"]
    repaired_patch = _joint_patch()
    repaired_patch["deferred_items"] = [
        {"item_id": "I2", "reason": "No compatible region value is known."}
    ]
    client = _StubClient(
        [
            _llm_response(plan),
            _llm_response(invalid_patch),
            _llm_response(repaired_patch),
        ]
    )

    result = OperationSmokeDiagnoser(
        client=client,
        planning_model=_planning_model(),
        patch_model=_patch_model(),
    ).diagnose(report=report, config=smoke_config())

    assert result.status == "patch_ready"
    assert result.covered_item_ids == ["I1"]
    assert result.deferred_items[0].item_id == "I2"
    assert "I2 is covered but none of its affected inputs changed" in (
        client.requests[2].messages[-1].content
    )
