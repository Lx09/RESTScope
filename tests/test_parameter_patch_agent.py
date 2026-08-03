"""Behavioral contracts for LLM-led Parameter Patch construction."""

from __future__ import annotations

from contextlib import contextmanager

from restscope.llm import LLMModelConfig, LLMResponse, ToolCall

from tests._operation_smoke_dedup_solve_fixtures import smoke_config


class StubClient:
    """Return prepared FAST-model outputs and retain requests."""

    def __init__(self, responses: list[dict]) -> None:
        self.responses = list(responses)
        self.requests = []

    def invoke(self, request):
        """Return the next proposal or Review submission for its role."""
        self.requests.append(request)
        arguments = self.responses.pop(0)
        role = request.metadata["role"]
        tool_name = (
            "submit_parameter_patch_review"
            if role == "parameter_patch_review_agent"
            else "submit_parameter_patch_proposal"
        )
        return LLMResponse(
            provider="stub",
            model="fast-model",
            tool_calls=[
                ToolCall(
                    id=f"call_patch_{len(self.requests)}",
                    name=tool_name,
                    arguments=arguments,
                    provider="stub",
                )
            ],
            finish_reason="tool_calls",
        )


class StubReferenceValues:
    """Expose one deterministic observed value at the external-value boundary."""

    def values_for(self, strategy):
        """Return the value expected by the reference-backed sample."""
        return ["known-project"]


class StrictUnavailableThenJsonClient:
    """Fail the first strict request, then return legacy JSON decisions."""

    def __init__(self, responses: list[dict]) -> None:
        self.responses = list(responses)
        self.requests = []

    def invoke(self, request):
        """Expose one compatibility failure followed by content JSON."""
        from restscope.llm import StrictToolUnavailableError

        self.requests.append(request)
        if len(self.requests) == 1:
            raise StrictToolUnavailableError(
                "deepseek_strict_schema_or_route_rejected",
                "scripted Beta rejection",
            )
        arguments = self.responses.pop(0)
        if request.tools:
            return LLMResponse(
                provider="stub",
                model="fast-model",
                tool_calls=[
                    ToolCall(
                        id=f"call_{len(self.requests)}",
                        name=request.tools[0].name,
                        arguments=arguments,
                    )
                ],
                finish_reason="tool_calls",
            )
        return LLMResponse(
            provider="stub",
            model="fast-model",
            parsed_json=arguments,
            finish_reason="stop",
        )


class RawResponseClient:
    """Return complete prepared responses for tool-protocol edge cases."""

    def __init__(self, responses: list[LLMResponse]) -> None:
        self.responses = list(responses)
        self.requests = []

    def invoke(self, request):
        """Retain the request and return the next exact response."""
        self.requests.append(request)
        return self.responses.pop(0)


class PerRoleFallbackClient:
    """Make each strict Agent fall back once, then return legacy JSON."""

    def __init__(self, responses: list[dict]) -> None:
        self.responses = list(responses)
        self.requests = []
        self.failed_roles: set[str] = set()

    def invoke(self, request):
        """Fail the first strict request independently for each Agent role."""
        from restscope.llm import StrictToolUnavailableError

        self.requests.append(request)
        role = request.metadata["role"]
        if request.tools and role not in self.failed_roles:
            self.failed_roles.add(role)
            raise StrictToolUnavailableError(
                "deepseek_strict_schema_or_route_rejected",
                f"scripted {role} Beta rejection",
            )
        return LLMResponse(
            provider="stub",
            model="fast-model",
            parsed_json=self.responses.pop(0),
            finish_reason="stop",
        )


class CapturingSpan:
    """Collect trace attributes and output without an observability backend."""

    def __init__(self, name: str, attributes: dict | None) -> None:
        self.name = name
        self.attributes = dict(attributes or {})
        self.output = None

    def set_attribute(self, name, value) -> None:
        """Retain one trace attribute for an assertion."""
        self.attributes[name] = value

    def set_output(self, value) -> None:
        """Retain the bounded span result for an assertion."""
        self.output = value


class CapturingTracingRuntime:
    """Provide the small tracing Interface used by the Patch Module."""

    def __init__(self) -> None:
        self.spans: list[CapturingSpan] = []

    @contextmanager
    def span(self, name, *, kind, input_value=None, attributes=None):
        """Record a span while preserving the production context-manager shape."""
        del kind, input_value
        span = CapturingSpan(name, attributes)
        self.spans.append(span)
        yield span


def _model() -> LLMModelConfig:
    """Build the FAST role used by the Patch Agent."""
    return LLMModelConfig(
        role="parameter_patch_agent",
        provider="stub",
        model="fast-model",
        max_tokens=8192,
        context_window_tokens=131072,
    )


def _review_model() -> LLMModelConfig:
    """Build the separate FAST role used by the Review Agent."""
    return _model().model_copy(update={"role": "parameter_patch_review_agent"})


def _sampleable_config():
    """Add request serialization metadata to the compact shared fixture."""
    from restscope.testing import ParameterSnapshot

    config = smoke_config()
    return config.model_copy(
        update={
            "snapshot": config.snapshot.model_copy(
                update={
                    "parameters": [
                        ParameterSnapshot(
                            input_node_id="path/projectId",
                            name="projectId",
                            location="path",
                            required=True,
                        ),
                        ParameterSnapshot(
                            input_node_id="query/region",
                            name="region",
                            location="query",
                            required=False,
                        ),
                    ]
                }
            )
        }
    )


def _task():
    """Build one Solve-owned Patch requirement with no Group concepts."""
    from restscope.operation_smoke.parameter_patch import ParameterPatchTask

    return ParameterPatchTask(
        todo_id="T1",
        failure="Project lookup returns not found.",
        root_cause="The generated project identifier does not exist.",
        affected_inputs=["path.projectId"],
        desired_behavior="Generate a project identifier accepted by the API.",
        acceptance_criteria="The project-not-found response disappears.",
        prior_attempts=[],
    )


def _constant_patch(input_name: str = "path.projectId"):
    """Return one complete model-shaped constant Generator proposal."""
    return {
        "action": "propose",
        "patch": {
            "changes": [
                {
                    "input": input_name,
                    "strategy": {
                        "type": "constant",
                        "value": "known-project",
                    },
                }
            ],
            "constraints": [],
        },
    }


def _variant_config():
    """Build the GitLab-like string-or-integer project path Parameter."""
    from restscope.openapi_parser import OpenAPIParser
    from restscope.testing.snapshot import build_initial_operation_config

    operation = OpenAPIParser.parse(
        {
            "openapi": "3.0.3",
            "info": {"title": "Variant project ID", "version": "1"},
            "paths": {
                "/projects/{id}": {
                    "delete": {
                        "parameters": [
                            {
                                "name": "id",
                                "in": "path",
                                "required": True,
                                "schema": {
                                    "oneOf": [
                                        {"type": "string"},
                                        {"type": "integer"},
                                    ]
                                },
                            }
                        ],
                        "responses": {"202": {"description": "accepted"}},
                    }
                }
            },
        }
    ).operations["DELETE /projects/{id}"]
    return build_initial_operation_config(operation)


def _variant_task(*affected_inputs: str):
    """Describe a repair that must consistently generate a known project ID."""
    from restscope.operation_smoke.parameter_patch import ParameterPatchTask

    return ParameterPatchTask(
        todo_id="T-variant",
        failure="Random project identifiers return not found.",
        root_cause="Only an observed integer project identifier is accepted.",
        affected_inputs=list(affected_inputs),
        desired_behavior="Always generate the known integer project identifier.",
        acceptance_criteria="Every sample selects the integer branch with value 21.",
        prior_attempts=[],
    )


def _variant_patch(*, include_parent: bool) -> dict:
    """Build either the unsafe child-only proposal or its complete repair."""
    changes = [
        {
            "input": "path.id.oneOf[1]",
            "strategy": {"type": "constant", "value": 21},
        }
    ]
    if include_parent:
        changes.insert(
            0,
            {
                "input": "path.id",
                "strategy": {
                    "type": "variant",
                    "branch_weights": [0, 1],
                },
            },
        )
    return {
        "action": "propose",
        "patch": {"changes": changes, "constraints": []},
    }


def test_patch_strict_tool_schema_preserves_wire_shape_and_server_rules() -> None:
    """The strict schema keeps Patch decisions while using only Beta keywords."""
    from jsonschema import Draft202012Validator

    from restscope.operation_smoke.parameter_patch.decision_tool import (
        parameter_patch_proposal_tool_spec,
    )

    tool = parameter_patch_proposal_tool_spec()
    schema = tool.input_schema
    unsupported = {
        "default",
        "discriminator",
        "maxItems",
        "maxLength",
        "minItems",
        "minLength",
        "oneOf",
    }
    seen_unsupported: list[str] = []
    invalid_objects: list[str] = []

    def inspect(value: dict, path: str = "$") -> None:
        """Collect unsupported keywords and invalid DeepSeek schema nodes."""
        if not {"type", "anyOf", "$ref"} & set(value):
            invalid_objects.append(f"{path}:missing schema kind")
        seen_unsupported.extend(
            f"{path}.{key}" for key in value if key in unsupported
        )
        if value.get("type") == "object":
            properties = set(value.get("properties", {}))
            required = set(value.get("required", []))
            if properties != required or value.get("additionalProperties") is not False:
                invalid_objects.append(path)
        for name, item in value.get("properties", {}).items():
            inspect(item, f"{path}.properties.{name}")
        if isinstance(value.get("items"), dict):
            inspect(value["items"], f"{path}.items")
        for index, item in enumerate(value.get("anyOf", [])):
            inspect(item, f"{path}.anyOf[{index}]")
        for name, item in value.get("$defs", {}).items():
            inspect(item, f"{path}.$defs.{name}")

    inspect(schema)

    assert tool.strict is True
    assert schema["type"] == "object"
    assert set(schema["properties"]) == {"action", "patch"}
    assert set(schema["required"]) == {"action", "patch"}
    assert not seen_unsupported
    assert not invalid_objects
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    validator.validate(_constant_patch())
    validator.validate(
        {
            "action": "propose",
            "patch": {
                "constraints": [
                    {
                        "expression": {
                            "type": "implies",
                            "condition": {
                                "type": "present",
                                "input": "path.projectId",
                            },
                            "consequence": {
                                "type": "compare",
                                "operator": "==",
                                "left": {
                                    "type": "input_value",
                                    "input": "path.projectId",
                                },
                                "right": {
                                    "type": "literal",
                                    "value": ["known-project", None],
                                },
                            },
                        }
                    }
                ]
            },
        }
    )

    from restscope.operation_smoke.parameter_patch.review import (
        parameter_patch_review_tool_spec,
    )

    review_schema = parameter_patch_review_tool_spec().input_schema
    assert review_schema["type"] == "object"
    assert set(review_schema["properties"]) == {"accepted", "issues"}
    assert set(review_schema["required"]) == {"accepted", "issues"}
    assert review_schema["additionalProperties"] is False
    Draft202012Validator.check_schema(review_schema)
    Draft202012Validator(review_schema).validate(
        {"accepted": True, "issues": []}
    )


def test_patch_and_review_fall_back_independently() -> None:
    """Each Agent gets one strict-to-legacy fallback without extra outputs."""
    from restscope.operation_smoke.parameter_patch import ParameterPatchCoordinator

    client = PerRoleFallbackClient(
        [_constant_patch(), {"accepted": True, "issues": []}]
    )
    outcome = ParameterPatchCoordinator(
        client=client,
        patch_model=_model(),
        review_model=_review_model(),
    ).run(
        task=_task(),
        config=_sampleable_config(),
        active_constraints=[],
        case_count=1,
        max_outputs=2,
    )

    assert outcome.status == "validated"
    assert outcome.outputs_used == 2
    assert client.failed_roles == {
        "parameter_patch_agent",
        "parameter_patch_review_agent",
    }
    assert len(client.requests) == 4


def test_patch_falls_back_once_then_keeps_legacy_json_for_the_session() -> None:
    """One strict compatibility failure does not recur after JSON fallback."""
    from restscope.operation_smoke.parameter_patch import (
        ParameterPatchCoordinator,
        ValidatedParameterPatch,
    )

    client = StrictUnavailableThenJsonClient(
        [_constant_patch(), {"accepted": True, "issues": []}]
    )
    outcome = ParameterPatchCoordinator(
        client=client,
        patch_model=_model(),
        review_model=_review_model(),
    ).run(
        task=_task(),
        config=_sampleable_config(),
        active_constraints=[],
        case_count=1,
        max_outputs=2,
    )

    assert isinstance(outcome, ValidatedParameterPatch)
    assert outcome.outputs_used == 2
    assert client.requests[0].tools[0].strict is True
    assert client.requests[0].tool_choice == "required"
    assert client.requests[1].tools == []
    assert client.requests[1].metadata["role"] == "parameter_patch_agent"
    assert client.requests[2].tools[0].name == "submit_parameter_patch_review"
    assert [item["transport"] for item in outcome.attempt_history] == [
        "legacy_json",
        "strict_tool",
    ]


def test_patch_rejects_wrong_or_multiple_submission_tools_safely() -> None:
    """Invalid tool groups are discarded instead of creating orphan history."""
    from restscope.operation_smoke.parameter_patch import ParameterPatchCoordinator

    wrong = LLMResponse(
        provider="stub",
        model="fast-model",
        tool_calls=[
            ToolCall(
                id="wrong-call",
                name="other_tool",
                arguments=_constant_patch(),
            )
        ],
    )
    multiple = LLMResponse(
        provider="stub",
        model="fast-model",
        tool_calls=[
            ToolCall(
                id="first-call",
                name="submit_parameter_patch_proposal",
                arguments=_constant_patch(),
            ),
            ToolCall(
                id="second-call",
                name="submit_parameter_patch_proposal",
                arguments=_constant_patch(),
            ),
        ],
    )
    client = RawResponseClient([wrong, multiple])

    outcome = ParameterPatchCoordinator(
        client=client,
        patch_model=_model(),
        review_model=_review_model(),
    ).run(
        task=_task(),
        config=_sampleable_config(),
        active_constraints=[],
        case_count=1,
        max_outputs=2,
    )

    assert outcome.status == "failed"
    assert outcome.reason == "output_budget_exhausted"
    assert "exactly one" in outcome.errors[0]
    # Neither rejected assistant call is replayed. Each new request therefore
    # contains only trusted user correction text after the initial task.
    assert all(
        all(not message.tool_calls for message in request.messages)
        for request in client.requests[1:]
    )


def test_patch_uses_case_count_in_a_fresh_review_context() -> None:
    """The Reviewer sees samples but no Patch Agent conversation history."""
    from restscope.operation_smoke.parameter_patch import (
        AvailableReferenceOption,
        ParameterPatchCoordinator,
        ValidatedParameterPatch,
    )

    client = StubClient([_constant_patch(), {"accepted": True, "issues": []}])

    outcome = ParameterPatchCoordinator(
        client=client,
        patch_model=_model(),
        review_model=_review_model(),
    ).run(
        task=_task(),
        config=_sampleable_config(),
        active_constraints=[],
        case_count=3,
        max_outputs=20,
        reference_options=[
            AvailableReferenceOption(
                option_id="ref-a",
                input_node_id="path/projectId",
                kind="response_value",
                value_name="known_project_id",
                compatible_scalar_type="string",
                value_count=4,
                producer_operation_keys=["GET /projects"],
                producer_status_code="200",
                producer_media_type="application/json",
                source_field="id",
                source_selector="$[].id",
            )
        ],
    )

    assert isinstance(outcome, ValidatedParameterPatch)
    assert outcome.todo_id == "T1"
    assert outcome.outputs_used == 2
    assert len(outcome.samples) == 3
    assert all(
        sample["values"]["path.projectId"] == "known-project"
        for sample in outcome.samples
    )
    review_request = client.requests[1]
    assert review_request.metadata["role"] == "parameter_patch_review_agent"
    assert len(review_request.messages) == 2
    review_context = review_request.messages[1].content
    assert "GENERATED SAMPLES" in review_context
    assert "known-project" in review_context
    assert "PATCH PROPOSAL REJECTED" not in review_context
    initial = client.requests[0].messages[1].content
    assert "PATCH REQUIREMENT" in initial
    assert "CURRENT GENERATORS" in initial
    assert "status=string:\"200\"" in initial
    assert "media=string:\"application/json\"" in initial
    assert "selector=string:\"$[].id\"" in initial
    assert '{"' not in initial


def test_review_issues_return_to_the_original_patch_session_for_revision() -> None:
    """Reviewer rejection revises the Patch without sharing Reviewer dialogue."""
    from restscope.operation_smoke.parameter_patch import ParameterPatchCoordinator

    replacement = _constant_patch()
    replacement["patch"]["changes"][0]["strategy"]["value"] = "known-project-2"
    client = StubClient(
        [
            _constant_patch(),
            {
                "accepted": False,
                "issues": ["The constant does not satisfy the stated behavior."],
            },
            replacement,
            {"accepted": True, "issues": []},
        ]
    )

    outcome = ParameterPatchCoordinator(
        client=client,
        patch_model=_model(),
        review_model=_review_model(),
    ).run(
        task=_task(),
        config=_sampleable_config(),
        active_constraints=[],
        case_count=1,
        max_outputs=4,
    )

    assert outcome.status == "validated"
    assert outcome.outputs_used == 4
    revision_request = client.requests[2]
    feedback = revision_request.messages[-1]
    assert feedback.role == "tool"
    assert feedback.tool_call_id == "call_patch_1"
    assert "does not satisfy" in feedback.content
    assert all(
        call.name != "submit_parameter_patch_review"
        for message in revision_request.messages
        for call in message.tool_calls
    )


def test_empty_review_issues_override_a_false_accepted_flag() -> None:
    """Local normalization makes issues, not the raw boolean, authoritative."""
    from restscope.operation_smoke.parameter_patch import ParameterPatchCoordinator

    outcome = ParameterPatchCoordinator(
        client=StubClient(
            [_constant_patch(), {"accepted": False, "issues": []}]
        ),
        patch_model=_model(),
        review_model=_review_model(),
    ).run(
        task=_task(),
        config=_sampleable_config(),
        active_constraints=[],
        case_count=1,
        max_outputs=2,
    )

    assert outcome.status == "validated"
    review_attempt = outcome.attempt_history[-1]
    assert review_attempt["raw_accepted"] is False


def test_patch_traces_separate_coordinator_proposal_and_review() -> None:
    """Trace names and counters make the two model roles distinguishable."""
    from restscope.operation_smoke.parameter_patch import ParameterPatchCoordinator

    tracing = CapturingTracingRuntime()
    outcome = ParameterPatchCoordinator(
        client=StubClient(
            [_constant_patch(), {"accepted": True, "issues": []}]
        ),
        patch_model=_model(),
        review_model=_review_model(),
        tracing_runtime=tracing,
    ).run(
        task=_task(),
        config=_sampleable_config(),
        active_constraints=[],
        case_count=1,
        max_outputs=2,
    )

    assert outcome.status == "validated"
    assert [span.name for span in tracing.spans] == [
        "ParameterPatchCoordinator.run",
        "ParameterPatchAgent.propose",
        "ParameterPatchReviewAgent.run",
    ]
    coordinator, proposal, review = tracing.spans
    assert coordinator.output["outputs_used"] == 2
    assert proposal.output["transport"] == "strict_tool"
    assert review.attributes["restscope.patch.review.transport"] == "strict_tool"
    assert review.attributes["restscope.patch.review.issue_count"] == 0
    assert review.attributes["restscope.patch.shared_outputs_used"] == 2


def test_invalid_review_protocol_is_corrected_without_reproposing() -> None:
    """A Reviewer transport error stays inside the same fresh candidate review."""
    from restscope.operation_smoke.parameter_patch import ParameterPatchCoordinator

    client = RawResponseClient(
        [
            LLMResponse(
                provider="stub",
                model="fast-model",
                tool_calls=[
                    ToolCall(
                        id="proposal-1",
                        name="submit_parameter_patch_proposal",
                        arguments=_constant_patch(),
                    )
                ],
            ),
            LLMResponse(
                provider="stub",
                model="fast-model",
                tool_calls=[
                    ToolCall(
                        id="wrong-review",
                        name="other_tool",
                        arguments={"accepted": True, "issues": []},
                    )
                ],
            ),
            LLMResponse(
                provider="stub",
                model="fast-model",
                tool_calls=[
                    ToolCall(
                        id="review-2",
                        name="submit_parameter_patch_review",
                        arguments={"accepted": True, "issues": []},
                    )
                ],
            ),
        ]
    )

    outcome = ParameterPatchCoordinator(
        client=client,
        patch_model=_model(),
        review_model=_review_model(),
    ).run(
        task=_task(),
        config=_sampleable_config(),
        active_constraints=[],
        case_count=1,
        max_outputs=3,
    )

    assert outcome.status == "validated"
    assert outcome.outputs_used == 3
    assert [request.metadata["role"] for request in client.requests] == [
        "parameter_patch_agent",
        "parameter_patch_review_agent",
        "parameter_patch_review_agent",
    ]
    assert "REVIEW OUTPUT INVALID" in client.requests[2].messages[-1].content


def test_patch_repairs_a_nested_propose_wrapper_with_the_declared_schema() -> None:
    """A malformed live-style wrapper receives an explicit top-level correction."""
    from restscope.operation_smoke.parameter_patch import ParameterPatchCoordinator

    nested_wrapper = {
        "propose": {
            "action": "propose",
            "patch": _constant_patch()["patch"],
        }
    }
    client = StubClient(
        [nested_wrapper, _constant_patch(), {"accepted": True, "issues": []}]
    )

    outcome = ParameterPatchCoordinator(
        client=client,
        patch_model=_model(),
        review_model=_review_model(),
    ).run(
        task=_task(),
        config=_sampleable_config(),
        active_constraints=[],
        case_count=1,
        max_outputs=3,
    )

    assert outcome.status == "validated"
    assert outcome.outputs_used == 3
    first_request = client.requests[0]
    assert first_request.response_format == "text"
    assert first_request.tool_choice == "required"
    assert first_request.reasoning.mode == "disabled"
    assert [tool.name for tool in first_request.tools] == [
        "submit_parameter_patch_proposal"
    ]
    assert first_request.tools[0].strict is True
    correction = client.requests[1].messages[-1].content
    assert client.requests[1].messages[-1].role == "tool"
    assert client.requests[1].messages[-1].tool_call_id == "call_patch_1"
    assert correction.startswith("## PATCH PROPOSAL REJECTED — UNTRUSTED")
    assert 'Use action="propose"' in correction
    assert "Submit one complete replacement patch" in correction


def test_patch_repairs_a_reference_alias_embedded_in_strategy() -> None:
    """A supplied R alias belongs beside the semantic input, not in strategy."""
    from restscope.operation_smoke.parameter_patch import (
        AvailableReferenceOption,
        ParameterPatchCoordinator,
    )

    option = AvailableReferenceOption(
        option_id="ref-a",
        input_node_id="path/projectId",
        kind="response_value",
        value_name="known_project_id",
        compatible_scalar_type="string",
        value_count=1,
        producer_operation_keys=["GET /projects"],
        producer_status_code="200",
        producer_media_type="application/json",
        source_field="id",
        source_selector="$[].id",
    )
    invalid_reference = {
        "action": "propose",
        "patch": {
            "changes": [
                {
                    "input": "path.projectId",
                    "strategy": {
                        "type": "response_value",
                        "reference": "R1",
                    },
                }
            ],
            "constraints": [],
        },
    }
    valid_reference = {
        "action": "propose",
        "patch": {
            "changes": [
                {
                    "input": "path.projectId",
                    "reference": "R1",
                }
            ],
            "constraints": [],
        },
    }
    client = StubClient(
        [invalid_reference, valid_reference, {"accepted": True, "issues": []}]
    )

    outcome = ParameterPatchCoordinator(
        client=client,
        patch_model=_model(),
        review_model=_review_model(),
    ).run(
        task=_task(),
        config=_sampleable_config(),
        active_constraints=[],
        case_count=1,
        reference_values=StubReferenceValues(),
        reference_options=[option],
        max_outputs=3,
    )

    assert outcome.status == "validated"
    assert outcome.outputs_used == 3
    assert outcome.patch.updates[0].strategy.type == "response_value"
    correction = client.requests[1].messages[-1].content
    assert "set reference beside input and omit strategy" in correction


def test_variant_child_patch_requires_explicit_parent_branch_selection() -> None:
    """A child-only fix cannot pass while another branch remains reachable."""
    from restscope.operation_smoke.parameter_patch import ParameterPatchCoordinator

    outcome = ParameterPatchCoordinator(
        client=StubClient([_variant_patch(include_parent=False)]),
        patch_model=_model(),
        review_model=_review_model(),
    ).run(
        task=_variant_task("path.id.oneOf[1]"),
        config=_variant_config(),
        active_constraints=[],
        case_count=10,
        random_seed=20260730,
        max_outputs=1,
    )

    assert outcome.status == "failed"
    assert any(
        "path.id" in error and "branch" in error
        for error in outcome.errors
    )


def test_complete_variant_patch_always_samples_the_selected_branch() -> None:
    """Parent weights plus the child Generator make every sample deterministic."""
    from restscope.operation_smoke.parameter_patch import ParameterPatchCoordinator

    outcome = ParameterPatchCoordinator(
        client=StubClient(
            [_variant_patch(include_parent=True), {"accepted": True, "issues": []}]
        ),
        patch_model=_model(),
        review_model=_review_model(),
    ).run(
        task=_variant_task("path.id", "path.id.oneOf[1]"),
        config=_variant_config(),
        active_constraints=[],
        case_count=10,
        random_seed=20260730,
        max_outputs=2,
    )

    assert outcome.status == "validated"
    assert all(
        sample["present"]["path.id.oneOf[1]"]
        and sample["values"]["path.id.oneOf[1]"] == 21
        for sample in outcome.samples
    )


def test_patch_cannot_change_input_outside_solve_requirement() -> None:
    """Scenario: executable safety rejects an input not authorized by Solve."""
    from restscope.operation_smoke.parameter_patch import ParameterPatchCoordinator

    client = StubClient(
        [
            _constant_patch("query.region"),
            _constant_patch(),
            {"accepted": True, "issues": []},
        ]
    )

    outcome = ParameterPatchCoordinator(
        client=client,
        patch_model=_model(),
        review_model=_review_model(),
    ).run(
        task=_task(),
        config=_sampleable_config(),
        active_constraints=[],
        case_count=2,
        max_outputs=3,
    )

    assert outcome.status == "validated"
    assert outcome.outputs_used == 3
    assert "outside the Solve Patch requirement" in (
        client.requests[1].messages[-1].content
    )


def test_patch_rejects_a_review_shape_submitted_as_a_proposal() -> None:
    """The Patch Agent has no model-side acceptance branch anymore."""
    from restscope.operation_smoke.parameter_patch import ParameterPatchCoordinator

    client = StubClient(
        [
            {"accepted": True, "issues": []},
            _constant_patch(),
            {"accepted": True, "issues": []},
        ]
    )

    outcome = ParameterPatchCoordinator(
        client=client,
        patch_model=_model(),
        review_model=_review_model(),
    ).run(
        task=_task(),
        config=_sampleable_config(),
        active_constraints=[],
        case_count=1,
        max_outputs=3,
    )

    assert outcome.status == "validated"
    assert outcome.outputs_used == 3
    assert len(outcome.attempt_history) == 3
    assert 'Use action="propose"' in client.requests[1].messages[-1].content


def test_patch_output_budget_returns_complete_failure_to_solve() -> None:
    """Scenario: every invalid Patch output counts toward the 20-output bound."""
    from restscope.operation_smoke.parameter_patch import (
        ParameterPatchCoordinator,
        ParameterPatchFailure,
    )

    client = StubClient([{"invalid": True}, {"invalid": True}])

    outcome = ParameterPatchCoordinator(
        client=client,
        patch_model=_model(),
        review_model=_review_model(),
    ).run(
        task=_task(),
        config=_sampleable_config(),
        active_constraints=[],
        case_count=2,
        max_outputs=2,
    )

    assert isinstance(outcome, ParameterPatchFailure)
    assert outcome.reason == "output_budget_exhausted"
    assert outcome.outputs_used == 2
    assert outcome.errors


def test_patch_stops_after_three_equivalent_invalid_outputs() -> None:
    """Repeated malformed decisions stop one tool session before its full budget."""
    from restscope.operation_smoke.parameter_patch import (
        ParameterPatchCoordinator,
        ParameterPatchFailure,
    )

    repeated = {
        "propose": {
            "action": "propose",
            "patch": _constant_patch()["patch"],
        }
    }
    client = StubClient([repeated, repeated, repeated])

    outcome = ParameterPatchCoordinator(
        client=client,
        patch_model=_model(),
        review_model=_review_model(),
    ).run(
        task=_task(),
        config=_sampleable_config(),
        active_constraints=[],
        case_count=1,
        max_outputs=20,
    )

    assert isinstance(outcome, ParameterPatchFailure)
    assert outcome.reason == "repeated_invalid_output"
    assert outcome.outputs_used == 3
    assert len(outcome.attempt_history) == 3
    assert len(client.requests) == 3


def test_patch_stops_after_three_equivalent_task_boundary_failures() -> None:
    """A valid DTO that repeats the same unsafe Patch must also stop early.

    The proposal parses correctly, but it changes an input that Solve did not
    authorize. Repeating it cannot produce new compiler evidence, so consuming
    the remaining 20-output Patch budget would only delay the parent Solve
    session.
    """
    from restscope.operation_smoke.parameter_patch import (
        ParameterPatchCoordinator,
        ParameterPatchFailure,
    )

    repeated = _constant_patch("query.region")
    client = StubClient([repeated, repeated, repeated])

    outcome = ParameterPatchCoordinator(
        client=client,
        patch_model=_model(),
        review_model=_review_model(),
    ).run(
        task=_task(),
        config=_sampleable_config(),
        active_constraints=[],
        case_count=1,
        max_outputs=20,
    )

    assert isinstance(outcome, ParameterPatchFailure)
    assert outcome.reason == "repeated_invalid_output"
    assert outcome.outputs_used == 3
    assert outcome.errors == [
        "query.region is outside the Solve Patch requirement"
    ]
    assert len(client.requests) == 3


def test_patch_stops_after_three_equivalent_review_rejections() -> None:
    """The same proposal and semantic issues trigger the shared three-strike guard."""
    from restscope.operation_smoke.parameter_patch import (
        ParameterPatchCoordinator,
        ParameterPatchFailure,
    )

    rejected_review = {
        "accepted": False,
        "issues": ["The proposal does not satisfy the acceptance criteria."],
    }
    client = StubClient(
        [
            _constant_patch(),
            rejected_review,
            _constant_patch(),
            rejected_review,
            _constant_patch(),
            rejected_review,
        ]
    )

    outcome = ParameterPatchCoordinator(
        client=client,
        patch_model=_model(),
        review_model=_review_model(),
    ).run(
        task=_task(),
        config=_sampleable_config(),
        active_constraints=[],
        case_count=1,
        max_outputs=20,
    )

    assert isinstance(outcome, ParameterPatchFailure)
    assert outcome.reason == "repeated_invalid_output"
    assert outcome.outputs_used == 6
    assert len(client.requests) == 6


def test_patch_keeps_constraint_compilation_as_executable_boundary() -> None:
    """Scenario: an unsatisfiable Constraint never reaches real HTTP execution."""
    from restscope.operation_smoke.parameter_patch import ParameterPatchCoordinator

    impossible = {
        "action": "propose",
        "patch": {
            "changes": [],
            "constraints": [
                {
                    "expression": {
                        "type": "and",
                        "expressions": [
                            {"type": "present", "input": "path.projectId"},
                            {
                                "type": "not",
                                "expression": {
                                    "type": "present",
                                    "input": "path.projectId",
                                },
                            },
                        ],
                    }
                }
            ],
        },
    }
    client = StubClient([impossible, impossible])

    outcome = ParameterPatchCoordinator(
        client=client,
        patch_model=_model(),
        review_model=_review_model(),
    ).run(
        task=_task(),
        config=_sampleable_config(),
        active_constraints=[],
        case_count=2,
        max_outputs=2,
    )

    assert outcome.status == "failed"
    assert any(
        "satisf" in error.lower() or "constraint" in error.lower()
        for error in outcome.errors
    )


def test_patch_replaces_an_overlapping_active_constraint_before_sampling() -> None:
    """A new Constraint replaces an active owner before local sample review.

    The active presence rule and the proposed value rule both own the project
    path Parameter. Parameter Patch must preview the same owner replacement
    that persistence will apply, then sample only the proposed rule.
    """
    from restscope.operation_smoke.parameter_patch import (
        CompiledConstraintPatch,
        ParameterPatchCoordinator,
        ValidatedParameterPatch,
    )
    from restscope.testing import ConstraintSet, PresentPredicate

    active = CompiledConstraintPatch(
        constraint_id="constraint_active_presence",
        kind="Complex",
        constraint=ConstraintSet(
            constraints=[
                PresentPredicate(
                    type="present",
                    input_node_id="path/projectId",
                )
            ]
        ),
    )
    proposal = _constant_patch()
    proposal["patch"]["constraints"] = [
        {
            "expression": {
                "type": "compare",
                "operator": "==",
                "left": {
                    "type": "input_value",
                    "input": "path.projectId",
                },
                "right": {"type": "literal", "value": "known-project"},
            }
        }
    ]
    client = StubClient([proposal, {"accepted": True, "issues": []}])

    outcome = ParameterPatchCoordinator(
        client=client,
        patch_model=_model(),
        review_model=_review_model(),
    ).run(
        task=_task(),
        config=_sampleable_config(),
        active_constraints=[active],
        case_count=2,
        max_outputs=2,
    )

    assert isinstance(outcome, ValidatedParameterPatch)
    assert outcome.outputs_used == 2
    assert len(outcome.patch.constraints) == 1
    assert all(
        sample["values"]["path.projectId"] == "known-project"
        for sample in outcome.samples
    )


def test_patch_requires_case_count_within_testing_boundary() -> None:
    """Scenario: local review uses the same 1-20 case limit as Smoke execution."""
    import pytest

    from restscope.operation_smoke.parameter_patch import ParameterPatchCoordinator

    with pytest.raises(ValueError, match="case_count"):
        ParameterPatchCoordinator(
            client=StubClient([]),
            patch_model=_model(),
            review_model=_review_model(),
        ).run(
            task=_task(),
            config=_sampleable_config(),
            active_constraints=[],
            case_count=21,
            max_outputs=20,
        )


def test_patch_uses_an_explicit_complete_system_prompt_override() -> None:
    """Scenario: evaluation can compare one candidate Patch prompt in isolation."""
    from restscope.operation_smoke.parameter_patch import ParameterPatchCoordinator

    client = StubClient([_constant_patch(), {"accepted": True, "issues": []}])

    ParameterPatchCoordinator(
        client=client,
        patch_model=_model(),
        review_model=_review_model(),
        patch_system_prompt="Candidate Patch instructions.",
    ).run(
        task=_task(),
        config=_sampleable_config(),
        active_constraints=[],
        case_count=1,
    )

    assert client.requests[0].messages[0].content == (
        "Candidate Patch instructions."
    )
