from __future__ import annotations

import pytest

from tests._operation_smoke_plan_solve_fixtures import smoke_config


class StubClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def invoke(self, request):
        self.requests.append(request)
        return self.responses.pop(0)


class RecordingSpan:
    def __init__(self):
        self.output = None
        self.attributes = {}

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def set_output(self, value):
        self.output = value

    def set_attribute(self, name, value):
        self.attributes[name] = value


class RecordingTracingRuntime:
    def __init__(self):
        self.spans = []

    def span(self, *args, **kwargs):
        del args, kwargs
        span = RecordingSpan()
        self.spans.append(span)
        return span


def llm_response(payload):
    from restscope.llm import LLMResponse

    return LLMResponse(
        provider="stub",
        model="fast-model",
        parsed_json=payload,
    )


def patch_model():
    from restscope.llm import LLMModelConfig

    return LLMModelConfig(
        role="parameter_patch_agent",
        provider="stub",
        model="fast-model",
        reasoning={"mode": "disabled"},
    )


def sampleable_config():
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


def group_task():
    from restscope.agent.parameter_patch import PatchGroupTask

    return PatchGroupTask(
        group_id="G1",
        item_ids=["I1"],
        root_failure_refs=["F1"],
        inputs=["path.projectId"],
        objective="Generate an existing project identifier.",
        requirements=[
            "path.projectId must use a value accepted by the API."
        ],
        candidate_hints=["known-project"],
        interaction_notes=[],
    )


def constant_patch():
    return {
        "action": "propose",
        "patch": {
            "changes": [
                {
                    "input": "path.projectId",
                    "strategy": {
                        "type": "constant",
                        "value": "known-project",
                    },
                }
            ],
            "constraints": [],
        },
    }


def test_agent_validates_samples_then_accepts_complete_patch() -> None:
    from restscope.agent.parameter_patch import ParameterPatchAgent

    client = StubClient(
        [
            llm_response(constant_patch()),
            llm_response({"action": "accept"}),
        ]
    )
    agent = ParameterPatchAgent(client=client, model=patch_model())

    result = agent.run(
        task=group_task(),
        config=sampleable_config(),
        active_constraints=[],
        max_attempts=20,
    )

    assert result.status == "validated"
    assert result.group_id == "G1"
    assert result.attempts == 2
    assert len(result.samples) == 10
    assert result.samples == [
        {
            "values": {"path.projectId": "known-project"},
            "present": {"path.projectId": True},
        }
        for _ in range(10)
    ]
    assert result.patch.updates[0].input_node_id == "path/projectId"
    assert result.patch.attributions[0].group_ids == ["G1"]
    assert "10 generated parameter value groups" in (
        client.requests[1].messages[-1].content
    )


def test_local_samples_project_array_values_and_parameter_presence() -> None:
    from restscope.agent.parameter_patch import (
        ParameterPatchAgent,
        PatchGroupTask,
    )
    from restscope.testing import (
        InputGeneratorConfig,
        InputNodeSnapshot,
        OperationGeneratorConfig,
        OperationTestSnapshot,
        ParameterSnapshot,
        SchemaSnapshot,
    )

    config = OperationGeneratorConfig(
        operation_key="GET /search",
        revision=1,
        snapshot=OperationTestSnapshot(
            operation_key="GET /search",
            method="GET",
            path="/search",
            parameters=[
                ParameterSnapshot(
                    input_node_id="query/tags",
                    name="tags",
                    location="query",
                    required=True,
                )
            ],
            input_nodes=[
                InputNodeSnapshot(
                    input_node_id="query/tags",
                    node_kind="parameter",
                    canonical_path="query/tags",
                    required=True,
                    schema_contract=SchemaSnapshot(
                        type="array",
                        items=SchemaSnapshot(type="string"),
                    ),
                ),
                InputNodeSnapshot(
                    input_node_id="query/tags/items",
                    node_kind="array_item",
                    canonical_path="query/tags/items",
                    parent_node_id="query/tags",
                    required=True,
                    schema_contract=SchemaSnapshot(type="string"),
                ),
            ],
        ),
        configs=[
            InputGeneratorConfig(
                input_node_id="query/tags",
                inclusion_probability=1,
                strategy={"type": "array", "min_items": 1, "max_items": 1},
            ),
            InputGeneratorConfig(
                input_node_id="query/tags/items",
                inclusion_probability=1,
                strategy={"type": "constant", "value": "tag"},
            ),
        ],
    )
    task = PatchGroupTask(
        group_id="G-array",
        item_ids=["I-array"],
        root_failure_refs=["F1"],
        inputs=["query.tags"],
        objective="Generate exactly two tags.",
        requirements=["query.tags contains exactly two values."],
    )
    client = StubClient(
        [
            llm_response(
                {
                    "action": "propose",
                    "patch": {
                        "changes": [
                            {
                                "input": "query.tags",
                                "strategy": {
                                    "type": "array",
                                    "min_items": 2,
                                    "max_items": 2,
                                },
                            }
                        ],
                        "constraints": [],
                    },
                }
            ),
            llm_response({"action": "accept"}),
        ]
    )

    result = ParameterPatchAgent(client=client, model=patch_model()).run(
        task=task,
        config=config,
        active_constraints=[],
    )

    assert result.status == "validated"
    assert result.samples == [
        {
            "values": {"query.tags": ["tag", "tag"]},
            "present": {"query.tags": True},
        }
        for _ in range(10)
    ]


def test_expert_prompt_contains_complete_generator_and_constraint_catalogs() -> None:
    from restscope.agent.parameter_patch import ParameterPatchAgent

    client = StubClient(
        [
            llm_response(constant_patch()),
            llm_response({"action": "accept"}),
        ]
    )

    ParameterPatchAgent(client=client, model=patch_model()).run(
        task=group_task(),
        config=sampleable_config(),
        active_constraints=[],
    )

    prompt = client.requests[0].messages[0].content
    for generator in (
        "constant",
        "choice",
        "integer_range",
        "number_range",
        "random_string",
        "boolean",
        "format",
        "object",
        "array",
        "variant",
        "resource_identifier",
        "response_value",
        "request_body",
    ):
        assert generator in prompt
    for constraint in (
        "present",
        "input_value",
        "literal",
        "arithmetic",
        "compare",
        "matches",
        "implies",
        "cardinality",
        "and",
        "or",
        "not",
    ):
        assert constraint in prompt
    for protocol in (
        "constant fields: type, value.",
        "choice fields: type, values, optional weights.",
        "integer_range fields: type, minimum, maximum.",
        "number_range fields: type, minimum, maximum.",
        "random_string fields: type, min_length, max_length, alphabet.",
        "boolean fields: type, true_probability.",
        "format fields: type, format.",
        "array fields: type, min_items, max_items.",
        "variant fields: type, branch_weights.",
        "input_value fields: type, input.",
        "literal fields: type, value.",
        "arithmetic fields: type, operator, left, right.",
        "compare fields: type, operator, left, right.",
        "matches fields: type, value, pattern.",
        "implies fields: type, condition, consequence.",
        "cardinality fields: type, expressions, minimum, maximum.",
        "and/or fields: type, expressions.",
        "not fields: type, expression.",
        "inclusion_probability must be between 0 and 1",
        "Every propose output is a complete replacement",
    ):
        assert protocol in prompt
    assert "object and request_body are system-managed" in prompt
    assert "R aliases" in prompt


def test_accept_before_sample_feedback_is_rejected_and_repaired() -> None:
    from restscope.agent.parameter_patch import ParameterPatchAgent

    client = StubClient(
        [
            llm_response({"action": "accept"}),
            llm_response(constant_patch()),
            llm_response({"action": "accept"}),
        ]
    )
    tracing = RecordingTracingRuntime()

    result = ParameterPatchAgent(
        client=client,
        model=patch_model(),
        tracing_runtime=tracing,
    ).run(
        task=group_task(),
        config=sampleable_config(),
        active_constraints=[],
    )

    assert result.status == "validated"
    assert result.attempts == 3
    assert "accept requires validated sample feedback" in (
        client.requests[1].messages[-1].content
    )


def test_reference_patch_keeps_system_option_and_reviews_raw_pool_values() -> None:
    from restscope.agent.parameter_patch import (
        AvailableReferenceOption,
        ParameterPatchAgent,
    )

    class ReferenceValues:
        def values_for(self, strategy):
            assert strategy.type == "resource_identifier"
            assert strategy.resource == "project"
            return ["observed-project"]

    option = AvailableReferenceOption(
        option_id="reference-project",
        input_node_id="path/projectId",
        kind="resource_identifier",
        canonical_resource="project",
        compatible_scalar_type="string",
        value_count=1,
    )
    client = StubClient(
        [
            llm_response(
                {
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
            ),
            llm_response({"action": "accept"}),
        ]
    )
    tracing = RecordingTracingRuntime()

    result = ParameterPatchAgent(
        client=client,
        model=patch_model(),
        tracing_runtime=tracing,
    ).run(
        task=group_task(),
        config=sampleable_config(),
        active_constraints=[],
        reference_values=ReferenceValues(),
        reference_options=[option],
    )

    assert result.status == "validated"
    assert result.patch.selected_reference_options == [option]
    assert result.samples == [
        {
            "values": {"path.projectId": "observed-project"},
            "present": {"path.projectId": True},
        }
        for _ in range(10)
    ]
    assert "observed-project" not in client.requests[0].messages[-1].content
    assert "observed-project" in client.requests[1].messages[-1].content
    assert tracing.spans[0].output["samples"][0] == {
        "values": {"path.projectId": "observed-project"},
        "present": {"path.projectId": True},
    }
    assert tracing.spans[0].output["reference_pool_values"] == {
        "R1": ["observed-project"]
    }


def test_agent_revision_replaces_previous_complete_patch_after_samples() -> None:
    from restscope.agent.parameter_patch import ParameterPatchAgent

    revised = constant_patch()
    revised["patch"]["changes"][0]["strategy"]["value"] = "revised-project"
    client = StubClient(
        [
            llm_response(constant_patch()),
            llm_response(revised),
            llm_response({"action": "accept"}),
        ]
    )

    result = ParameterPatchAgent(client=client, model=patch_model()).run(
        task=group_task(),
        config=sampleable_config(),
        active_constraints=[],
    )

    assert result.status == "validated"
    assert result.attempts == 3
    assert result.samples == [
        {
            "values": {"path.projectId": "revised-project"},
            "present": {"path.projectId": True},
        }
        for _ in range(10)
    ]


def test_invalid_revision_prevents_accepting_previous_sampled_patch() -> None:
    from restscope.agent.parameter_patch import ParameterPatchAgent

    invalid_revision = {
        "action": "propose",
        "patch": {
            "changes": [
                {
                    "input": "path.projectId",
                    "strategy": {"type": "object"},
                }
            ],
            "constraints": [],
        },
    }
    client = StubClient(
        [
            llm_response(constant_patch()),
            llm_response(invalid_revision),
            llm_response({"action": "accept"}),
        ]
    )

    result = ParameterPatchAgent(client=client, model=patch_model()).run(
        task=group_task(),
        config=sampleable_config(),
        active_constraints=[],
        max_attempts=3,
    )

    assert result.status == "failed"
    assert result.reason == "attempt_limit"
    assert result.attempts == 3


def test_unsatisfiable_constraint_exhausts_attempts_as_group_failure() -> None:
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
                            {
                                "type": "present",
                                "input": "path.projectId",
                            },
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
    client = StubClient(
        [
            llm_response(impossible),
            llm_response({"action": "accept"}),
        ]
    )

    result = ParameterPatchAgent(client=client, model=patch_model()).run(
        task=group_task(),
        config=sampleable_config(),
        active_constraints=[],
        max_attempts=2,
    )

    assert result.status == "failed"
    assert result.reason == "attempt_limit"
    assert result.attempts == 2


def test_system_managed_generator_is_repaired_with_a_complete_patch() -> None:
    from restscope.agent.parameter_patch import ParameterPatchAgent

    object_patch = {
        "action": "propose",
        "patch": {
            "changes": [
                {
                    "input": "path.projectId",
                    "strategy": {"type": "object"},
                }
            ],
            "constraints": [],
        },
    }
    client = StubClient(
        [
            llm_response(object_patch),
            llm_response(constant_patch()),
            llm_response({"action": "accept"}),
        ]
    )

    result = ParameterPatchAgent(client=client, model=patch_model()).run(
        task=group_task(),
        config=sampleable_config(),
        active_constraints=[],
    )

    assert result.status == "validated"
    assert result.attempts == 3
    assert "system-managed" in client.requests[1].messages[-1].content


def test_observed_generators_cannot_bypass_system_reference_aliases() -> None:
    from restscope.agent.parameter_patch import ParameterPatchAgent

    invented_reference = {
        "action": "propose",
        "patch": {
            "changes": [
                {
                    "input": "path.projectId",
                    "strategy": {
                        "type": "resource_identifier",
                        "resource": "invented-project",
                    },
                }
            ],
            "constraints": [],
        },
    }
    client = StubClient(
        [
            llm_response(invented_reference),
            llm_response(constant_patch()),
            llm_response({"action": "accept"}),
        ]
    )

    result = ParameterPatchAgent(client=client, model=patch_model()).run(
        task=group_task(),
        config=sampleable_config(),
        active_constraints=[],
    )

    assert result.status == "validated"
    assert "system-provided R alias" in (
        client.requests[1].messages[-1].content
    )


def test_every_model_output_counts_toward_twenty_attempt_limit() -> None:
    from restscope.agent.parameter_patch import ParameterPatchAgent

    client = StubClient([llm_response({}) for _ in range(20)])

    result = ParameterPatchAgent(client=client, model=patch_model()).run(
        task=group_task(),
        config=sampleable_config(),
        active_constraints=[],
        max_attempts=20,
    )

    assert result.status == "failed"
    assert result.reason == "attempt_limit"
    assert result.attempts == 20
    assert len(client.requests) == 20


def test_reference_provider_infrastructure_error_propagates() -> None:
    from restscope.agent.parameter_patch import (
        AvailableReferenceOption,
        ParameterPatchAgent,
    )

    class BrokenReferenceValues:
        def values_for(self, strategy):
            del strategy
            raise RuntimeError("reference database unavailable")

    client = StubClient(
        [
            llm_response(
                {
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
            )
        ]
    )
    option = AvailableReferenceOption(
        option_id="reference-project",
        input_node_id="path/projectId",
        kind="resource_identifier",
        canonical_resource="project",
        compatible_scalar_type="string",
        value_count=1,
    )

    with pytest.raises(
        RuntimeError,
        match="reference database unavailable",
    ):
        ParameterPatchAgent(client=client, model=patch_model()).run(
            task=group_task(),
            config=sampleable_config(),
            active_constraints=[],
            reference_values=BrokenReferenceValues(),
            reference_options=[option],
        )
