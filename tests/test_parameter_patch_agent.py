"""Behavioral contracts for LLM-led Parameter Patch construction."""

from __future__ import annotations

from restscope.llm import LLMModelConfig, LLMResponse

from tests._operation_smoke_dedup_solve_fixtures import smoke_config


class StubClient:
    """Return prepared FAST-model outputs and retain requests."""

    def __init__(self, responses: list[dict]) -> None:
        self.responses = list(responses)
        self.requests = []

    def invoke(self, request):
        """Return the next patch decision."""
        self.requests.append(request)
        return LLMResponse(
            provider="stub",
            model="fast-model",
            parsed_json=self.responses.pop(0),
        )


def _model() -> LLMModelConfig:
    """Build the FAST role used by the Patch Agent."""
    return LLMModelConfig(
        role="parameter_patch_agent",
        provider="stub",
        model="fast-model",
        max_tokens=8192,
        context_window_tokens=131072,
    )


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


def test_patch_uses_case_count_for_dynamic_samples_then_accepts() -> None:
    """Scenario: local review sample count follows the Smoke request."""
    from restscope.operation_smoke.parameter_patch import (
        AvailableReferenceOption,
        ParameterPatchAgent,
        ValidatedParameterPatch,
    )

    client = StubClient([_constant_patch(), {"action": "accept"}])

    outcome = ParameterPatchAgent(client=client, model=_model()).run(
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
    feedback = client.requests[1].messages[-1].content
    assert "samples=int:3" in feedback
    assert "path.projectId.present" in feedback
    assert "path.projectId.value" in feedback
    assert '{"' not in feedback
    initial = client.requests[0].messages[1].content
    assert "PATCH REQUIREMENT" in initial
    assert "CURRENT GENERATORS" in initial
    assert "status=string:\"200\"" in initial
    assert "media=string:\"application/json\"" in initial
    assert "selector=string:\"$[].id\"" in initial
    assert '{"' not in initial


def test_variant_child_patch_requires_explicit_parent_branch_selection() -> None:
    """A child-only fix cannot pass while another branch remains reachable."""
    from restscope.operation_smoke.parameter_patch import ParameterPatchAgent

    outcome = ParameterPatchAgent(
        client=StubClient([_variant_patch(include_parent=False)]),
        model=_model(),
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
    from restscope.operation_smoke.parameter_patch import ParameterPatchAgent

    outcome = ParameterPatchAgent(
        client=StubClient(
            [_variant_patch(include_parent=True), {"action": "accept"}]
        ),
        model=_model(),
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
    from restscope.operation_smoke.parameter_patch import ParameterPatchAgent

    client = StubClient(
        [
            _constant_patch("query.region"),
            _constant_patch(),
            {"action": "accept"},
        ]
    )

    outcome = ParameterPatchAgent(client=client, model=_model()).run(
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


def test_patch_accept_before_local_samples_consumes_output_and_repairs() -> None:
    """Scenario: the model must review compiler-backed samples before acceptance."""
    from restscope.operation_smoke.parameter_patch import ParameterPatchAgent

    client = StubClient(
        [
            {"action": "accept"},
            _constant_patch(),
            {"action": "accept"},
        ]
    )

    outcome = ParameterPatchAgent(client=client, model=_model()).run(
        task=_task(),
        config=_sampleable_config(),
        active_constraints=[],
        case_count=1,
        max_outputs=3,
    )

    assert outcome.status == "validated"
    assert outcome.outputs_used == 3
    assert len(outcome.attempt_history) == 3
    assert "requires compiled sample feedback" in (
        client.requests[1].messages[-1].content
    )


def test_patch_output_budget_returns_complete_failure_to_solve() -> None:
    """Scenario: every invalid Patch output counts toward the 20-output bound."""
    from restscope.operation_smoke.parameter_patch import (
        ParameterPatchAgent,
        ParameterPatchFailure,
    )

    client = StubClient([{"invalid": True}, {"invalid": True}])

    outcome = ParameterPatchAgent(client=client, model=_model()).run(
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


def test_patch_keeps_constraint_compilation_as_executable_boundary() -> None:
    """Scenario: an unsatisfiable Constraint never reaches real HTTP execution."""
    from restscope.operation_smoke.parameter_patch import ParameterPatchAgent

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

    outcome = ParameterPatchAgent(client=client, model=_model()).run(
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


def test_patch_requires_case_count_within_testing_boundary() -> None:
    """Scenario: local review uses the same 1-20 case limit as Smoke execution."""
    import pytest

    from restscope.operation_smoke.parameter_patch import ParameterPatchAgent

    with pytest.raises(ValueError, match="case_count"):
        ParameterPatchAgent(client=StubClient([]), model=_model()).run(
            task=_task(),
            config=_sampleable_config(),
            active_constraints=[],
            case_count=21,
            max_outputs=20,
        )


def test_patch_uses_an_explicit_complete_system_prompt_override() -> None:
    """Scenario: evaluation can compare one candidate Patch prompt in isolation."""
    from restscope.operation_smoke.parameter_patch import ParameterPatchAgent

    client = StubClient([_constant_patch(), {"action": "accept"}])

    ParameterPatchAgent(
        client=client,
        model=_model(),
        system_prompt="Candidate Patch instructions.",
    ).run(
        task=_task(),
        config=_sampleable_config(),
        active_constraints=[],
        case_count=1,
    )

    assert client.requests[0].messages[0].content == (
        "Candidate Patch instructions."
    )
