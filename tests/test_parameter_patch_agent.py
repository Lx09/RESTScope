"""Behavioral contracts for LLM-led Parameter Patch construction."""

from __future__ import annotations

from restscope.llm import LLMModelConfig, LLMResponse

from tests._operation_smoke_plan_solve_fixtures import smoke_config


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
    from restscope.agent.parameter_patch import ParameterPatchTask

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


def test_patch_uses_case_count_for_dynamic_samples_then_accepts() -> None:
    """Scenario: local review sample count follows the Smoke request."""
    from restscope.agent.parameter_patch import (
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
    )

    assert isinstance(outcome, ValidatedParameterPatch)
    assert outcome.todo_id == "T1"
    assert outcome.outputs_used == 2
    assert len(outcome.samples) == 3
    assert all(
        sample["values"]["path.projectId"] == "known-project"
        for sample in outcome.samples
    )
    assert "exactly 3 generated" in client.requests[1].messages[-1].content


def test_patch_cannot_change_input_outside_solve_requirement() -> None:
    """Scenario: executable safety rejects an input not authorized by Solve."""
    from restscope.agent.parameter_patch import ParameterPatchAgent

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
    from restscope.agent.parameter_patch import ParameterPatchAgent

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
    from restscope.agent.parameter_patch import (
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
    from restscope.agent.parameter_patch import ParameterPatchAgent

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

    from restscope.agent.parameter_patch import ParameterPatchAgent

    with pytest.raises(ValueError, match="case_count"):
        ParameterPatchAgent(client=StubClient([]), model=_model()).run(
            task=_task(),
            config=_sampleable_config(),
            active_constraints=[],
            case_count=21,
            max_outputs=20,
        )
