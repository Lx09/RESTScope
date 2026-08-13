"""Protect positive/negative Generator initialization and selection contracts."""

from __future__ import annotations

import pytest


def test_initial_state_keeps_one_positive_and_derives_scalar_negative_generators() -> None:
    """Each input starts with today's Generator plus deterministic invalid alternatives."""
    from restscope.openapi_parser import OpenAPIParser
    from restscope.request_generation import RequestGenerationConfigStore

    ir = OpenAPIParser.parse(
        {
            "openapi": "3.0.3",
            "info": {"title": "Generator exploration", "version": "1"},
            "paths": {
                "/items": {
                    "get": {
                        "parameters": [
                            {
                                "name": "limit",
                                "in": "query",
                                "required": True,
                                "schema": {
                                    "type": "integer",
                                    "minimum": 1,
                                    "maximum": 10,
                                },
                            }
                        ],
                        "responses": {"200": {"description": "ok"}},
                    }
                }
            },
        }
    )
    store = RequestGenerationConfigStore()

    assert store.initialize_once(ir) is True
    config = store.require_state("GET /items").config
    input_id = config.snapshot.parameters[0].input_node_id
    positives = [
        item for item in config.positive_generators if item.input_node_id == input_id
    ]
    negatives = [
        item for item in config.negative_generators if item.input_node_id == input_id
    ]

    assert len(positives) == 1
    assert positives[0].strategy.type == "integer_range"
    assert {item.rule for item in negatives} >= {
        "required_omission",
        "wrong_type",
        "below_minimum",
        "above_maximum",
    }


def test_run_batch_requires_the_main_agent_to_choose_a_test_mode() -> None:
    """The Tool never guesses whether a Batch is happy-path or exceptional."""
    from pydantic import ValidationError

    from restscope.tools.test_case.run_batch import RunBatchInput

    with pytest.raises(ValidationError):
        RunBatchInput(operation_key="GET /items")


def test_exceptional_negative_selection_drops_the_full_constraint_component() -> None:
    """A negative input removes every transitively associated Constraint at once."""
    from restscope.openapi_parser import OpenAPIParser
    from restscope.request_generation import RequestGenerationConfigStore
    from restscope.request_generation.constraints import (
        ConstraintSet,
        OperationConstraintRecord,
    )
    from restscope.request_generation.selection import (
        TestMode,
        choose_case_generators,
    )

    ir = OpenAPIParser.parse(
        {
            "openapi": "3.0.3",
            "info": {"title": "Constraint component", "version": "1"},
            "paths": {
                "/items": {
                    "get": {
                        "parameters": [
                            {
                                "name": name,
                                "in": "query",
                                "schema": {"type": "integer"},
                            }
                            for name in ("a", "b", "c", "unrelated")
                        ],
                        "responses": {"200": {"description": "ok"}},
                    }
                }
            },
        }
    )
    store = RequestGenerationConfigStore()
    store.initialize_once(ir)
    state = store.require_state("GET /items")
    ids = {
        parameter.name: parameter.input_node_id
        for parameter in state.config.snapshot.parameters
    }

    def relationship(identifier: str, left: str, right: str) -> OperationConstraintRecord:
        constraint = ConstraintSet.model_validate(
            {
                "constraints": [
                    {
                        "type": "compare",
                        "operator": "<=",
                        "left": {"type": "input_value", "input_node_id": ids[left]},
                        "right": {"type": "input_value", "input_node_id": ids[right]},
                    }
                ]
            }
        )
        return OperationConstraintRecord(
            id=identifier,
            operation_key=state.config.operation_key,
            owner_input_node_ids=[ids[left], ids[right]],
            kind="Arithmetic/Relational",
            constraint=constraint,
        )

    constraints = (
        relationship("ab", "a", "b"),
        relationship("bc", "b", "c"),
        relationship("u", "unrelated", "unrelated"),
    )
    selections = [
        choose_case_generators(
            config=state.config,
            constraints=constraints,
            test_mode=TestMode.EXCEPTIONAL,
            statistics={},
            run_seed=seed,
            case_index=0,
        )
        for seed in range(200)
    ]
    selected = next(
        item
        for item in selections
        if item is not None
        and item.action == "negative_generator"
        and item.negative_choice is not None
        and item.negative_choice.input_node_id == ids["a"]
    )

    assert selected.ignored_constraint_ids == ("ab", "bc")
    assert [item.id for item in selected.constraints] == ["u"]


def test_parameter_patch_replaces_one_inputs_complete_positive_candidate_set() -> None:
    """Repeated changes for one input form its full 1–8 positive arm set."""
    from restscope.openapi_parser import OpenAPIParser
    from restscope.request_generation import (
        RequestGenerationConfigStore,
        RequestGenerationPatchRuntime,
    )
    from restscope.request_generation.parameter_patch import SemanticParameterPatch

    ir = OpenAPIParser.parse(
        {
            "openapi": "3.0.3",
            "info": {"title": "Multiple positives", "version": "1"},
            "paths": {
                "/items": {
                    "get": {
                        "parameters": [
                            {
                                "name": "limit",
                                "in": "query",
                                "required": True,
                                "schema": {"type": "integer"},
                            }
                        ],
                        "responses": {"200": {"description": "ok"}},
                    }
                }
            },
        }
    )
    store = RequestGenerationConfigStore()
    store.initialize_once(ir)
    runtime = RequestGenerationPatchRuntime(store=store, ir_provider=lambda: ir)
    patch = SemanticParameterPatch.model_validate(
        {
            "changes": [
                {
                    "input": "query.limit",
                    "inclusion_probability": 1,
                    "strategy": {"type": "constant", "value": value},
                }
                for value in (1, 2)
            ]
        }
    )

    validated = runtime.validate(
        operation_key="GET /items",
        expected_revision=0,
        affected_inputs=("query.limit",),
        patch=patch,
        sample_count=2,
    )
    applied = runtime.apply(
        operation_key="GET /items",
        expected_revision=0,
        validation_digest=validated.validation_digest,
        affected_inputs=("query.limit",),
        patch=patch,
        sample_count=2,
    )
    input_id = applied.state.config.snapshot.parameters[0].input_node_id
    candidates = [
        item
        for item in applied.state.config.positive_generators
        if item.input_node_id == input_id
    ]

    assert [item.strategy.value for item in candidates] == [1, 2]


def test_positive_and_negative_rewards_update_separate_candidate_statistics() -> None:
    """2xx rewards positive arms; confirmed Bugs reward only negative arms."""
    from restscope.harness.operation_testing.outcomes import BatchCaseOutcome
    from restscope.harness.operation_testing.service import _case_feedback
    from restscope.request_generation.models import GeneratedTestCase
    from restscope.request_generation.selection import GeneratorChoice

    positive = GeneratorChoice(
        kind="positive",
        input_node_id="limit",
        candidate_id="positive-1",
    )
    negative = GeneratorChoice(
        kind="negative",
        input_node_id="limit",
        candidate_id="negative-1",
        rule="above_maximum",
    )
    generated = GeneratedTestCase(
        operation_key="GET /items",
        case_index=0,
        path_parameters={},
        query_parameters={"limit": 11},
        header_parameters={},
        cookie_parameters={},
        generated_values=[],
        omitted_input_node_ids=[],
    )
    # Feedback reads only selection identities; the frozen config is irrelevant
    # to this reward boundary and can be a lightweight test double.
    class Selection:
        action = "happy_path"
        positive_choices = (positive,)
        negative_choice = None

    happy = BatchCaseOutcome(
        case_number=1,
        test_action="happy_path",
        request={"path": {}, "query": {}, "header": {}, "cookie": {}},
        status_code=204,
    )
    assert _case_feedback(generated, Selection(), happy) == [(positive, 1)]

    class NegativeSelection:
        action = "negative_generator"
        positive_choices = ()
        negative_choice = negative

    bug = BatchCaseOutcome(
        case_number=1,
        test_action="negative_generator",
        negative_rule="above_maximum",
        bug_found=True,
        request={"path": {}, "query": {}, "header": {}, "cookie": {}},
        status_code=200,
    )
    assert _case_feedback(generated, NegativeSelection(), bug) == [(negative, 1)]
