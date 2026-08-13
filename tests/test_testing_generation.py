"""Regression scenarios for testing generation. Each test documents one observable contract or failure boundary."""

from __future__ import annotations

import re

import pytest


def _snapshot(operation):
    from restscope.request_generation.snapshot import build_operation_snapshot

    snapshot, _ = build_operation_snapshot(operation)
    return snapshot


@pytest.mark.parametrize(
    ("strategy", "assertion"),
    [
        ({"type": "constant", "value": "fixed"}, lambda value: value == "fixed"),
        ({"type": "choice", "values": ["a", "b"], "weights": [1, 3]}, lambda value: value in {"a", "b"}),
        ({"type": "integer_range", "minimum": 3, "maximum": 7}, lambda value: type(value) is int and 3 <= value <= 7),
        ({"type": "number_range", "minimum": 1.5, "maximum": 2.5}, lambda value: type(value) is float and 1.5 <= value <= 2.5),
        (
            {"type": "random_string", "min_length": 4, "max_length": 8, "alphabet": "ab"},
            lambda value: 4 <= len(value) <= 8 and set(value) <= {"a", "b"},
        ),
        ({"type": "boolean", "true_probability": 1}, lambda value: value is True),
        ({"type": "format", "format": "uuid"}, lambda value: bool(re.fullmatch(r"[0-9a-f-]{36}", value))),
        ({"type": "format", "format": "date"}, lambda value: bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", value))),
        (
            {"type": "format", "format": "date-time"},
            lambda value: value.endswith("Z") and "T" in value,
        ),
        ({"type": "format", "format": "email"}, lambda value: value.endswith("@example.test")),
    ],
)
def test_builtin_scalar_generators_are_deterministic_and_typed(strategy, assertion) -> None:
    """Scenario: verify that builtin scalar generators are deterministic and typed."""
    from restscope.request_generation.generation import generate_strategy_value
    from restscope.request_generation.models import InputGeneratorConfig

    config = InputGeneratorConfig(
        input_node_id="input_test",
        inclusion_probability=1,
        strategy=strategy,
    )

    first = generate_strategy_value(config.strategy, seed=9182)
    second = generate_strategy_value(config.strategy, seed=9182)

    assert first == second
    assert assertion(first)


def test_regex_generator_accepts_empty_pattern_and_uses_bounded_defaults() -> None:
    """Scenario: an empty regex remains valid and receives safe default lengths."""
    from restscope.request_generation.models import InputGeneratorConfig

    config = InputGeneratorConfig(
        input_node_id="input_regex",
        inclusion_probability=1,
        strategy={"type": "regex", "pattern": ""},
    )

    assert config.strategy.type == "regex"
    assert config.strategy.pattern == ""
    assert config.strategy.min_length == 0
    assert config.strategy.max_length == 100


def test_regex_generator_accepts_declared_upper_boundaries() -> None:
    """Scenario: the documented pattern and length maxima remain usable values."""
    from restscope.request_generation.models import InputGeneratorConfig

    config = InputGeneratorConfig(
        input_node_id="input_regex",
        inclusion_probability=1,
        strategy={
            "type": "regex",
            "pattern": "a" * 2000,
            "min_length": 10_000,
            "max_length": 10_000,
        },
    )

    assert len(config.strategy.pattern) == 2000
    assert config.strategy.min_length == config.strategy.max_length == 10_000


@pytest.mark.parametrize(
    ("strategy", "message"),
    [
        (
            {"type": "regex", "pattern": "["},
            "valid regular expression",
        ),
        (
            {
                "type": "regex",
                "pattern": "a",
                "min_length": 2,
                "max_length": 1,
            },
            "min_length cannot exceed max_length",
        ),
        (
            {"type": "regex", "pattern": "a" * 2001},
            "at most 2000 characters",
        ),
        (
            {"type": "regex", "pattern": "a", "max_length": 10001},
            "less than or equal to 10000",
        ),
    ],
)
def test_regex_generator_rejects_invalid_contracts(
    strategy: dict,
    message: str,
) -> None:
    """Scenario: malformed or unbounded regex contracts fail before generation."""
    from pydantic import ValidationError

    from restscope.request_generation.models import InputGeneratorConfig

    with pytest.raises(ValidationError, match=message):
        InputGeneratorConfig(
            input_node_id="input_regex",
            inclusion_probability=1,
            strategy=strategy,
        )


def test_regex_generator_is_seeded_and_produces_matching_values() -> None:
    """Scenario: one regex and seed always produce the same matching string."""
    from restscope.request_generation.generation import generate_strategy_value
    from restscope.request_generation.models import InputGeneratorConfig

    config = InputGeneratorConfig(
        input_node_id="input_regex",
        inclusion_probability=1,
        strategy={
            "type": "regex",
            "pattern": r"^[A-Z]{12}$",
            "min_length": 12,
            "max_length": 12,
        },
    )

    first = generate_strategy_value(config.strategy, seed=9182)
    repeated = generate_strategy_value(config.strategy, seed=9182)
    another = generate_strategy_value(config.strategy, seed=9183)

    assert first == repeated
    assert first != another
    assert re.search(config.strategy.pattern, first) is not None
    assert len(first) == 12


def test_regex_generator_seed_is_stable_across_hash_randomization() -> None:
    """Scenario: a negated character class produces the same value in new processes."""
    import os
    import subprocess
    import sys

    script = """
from restscope.request_generation.generation import generate_strategy_value
from restscope.request_generation.models import RegexGenerator

strategy = RegexGenerator(
    type="regex",
    pattern=r"^[^AB]{20}$",
    min_length=20,
    max_length=20,
)
print(generate_strategy_value(strategy, seed=9182).encode().hex())
"""

    def value_for_hash_seed(hash_seed: str) -> str:
        """Run generation in a fresh interpreter with one Python hash seed."""

        environment = os.environ.copy()
        environment["PYTHONHASHSEED"] = hash_seed
        completed = subprocess.run(
            [sys.executable, "-c", script],
            check=True,
            capture_output=True,
            text=True,
            env=environment,
        )
        return completed.stdout.splitlines()[-1]

    assert value_for_hash_seed("1") == value_for_hash_seed("2")


@pytest.mark.parametrize(
    "pattern",
    [
        "ABC",
        "^ABC",
        "ABC$",
    ],
)
def test_regex_generator_pads_short_search_matches(
    pattern: str,
) -> None:
    """Scenario: padding before or after a short match preserves search semantics."""
    from restscope.request_generation.generation import generate_strategy_value
    from restscope.request_generation.models import InputGeneratorConfig

    config = InputGeneratorConfig(
        input_node_id="input_regex",
        inclusion_probability=1,
        strategy={
            "type": "regex",
            "pattern": pattern,
            "min_length": 5,
            "max_length": 5,
        },
    )

    value = generate_strategy_value(config.strategy, seed=7)

    assert len(value) == 5
    assert re.search(pattern, value) is not None


@pytest.mark.parametrize(
    ("pattern", "min_length", "max_length"),
    [
        (r"^ABC$", 4, 4),
        (r"(?>ABC)", 3, 3),
        (r"a{101,}", 0, 200),
        (r"((a{100}){100}){100}", 0, 10),
    ],
)
def test_regex_generator_fails_closed_for_unsatisfied_or_unsupported_patterns(
    pattern: str,
    min_length: int,
    max_length: int,
) -> None:
    """Scenario: generation errors replace invalid, unsupported, or oversized output."""
    from restscope.request_generation.generation import GenerationError, generate_strategy_value
    from restscope.request_generation.models import InputGeneratorConfig

    config = InputGeneratorConfig(
        input_node_id="input_regex",
        inclusion_probability=1,
        strategy={
            "type": "regex",
            "pattern": pattern,
            "min_length": min_length,
            "max_length": max_length,
        },
    )

    with pytest.raises(GenerationError, match="Regex generator"):
        generate_strategy_value(config.strategy, seed=7)


def test_regex_generator_limits_work_for_large_empty_repetitions() -> None:
    """Scenario: empty repeated items still consume the finite generation budget."""
    from restscope.request_generation.generation import GenerationError, generate_strategy_value
    from restscope.request_generation.models import InputGeneratorConfig

    config = InputGeneratorConfig(
        input_node_id="input_regex",
        inclusion_probability=1,
        strategy={
            "type": "regex",
            "pattern": r"(?:){20001}",
            "max_length": 0,
        },
    )

    with pytest.raises(GenerationError, match="Regex generator"):
        generate_strategy_value(config.strategy, seed=7)


def test_patterned_text_body_uses_its_default_regex_generator() -> None:
    """Scenario: a text request body derives and executes one regex strategy."""
    from restscope.openapi_parser import OpenAPIParser
    from restscope.request_generation.generation import generate_test_case
    from restscope.request_generation.snapshot import build_initial_operation_config

    operation = OpenAPIParser.parse(
        {
            "openapi": "3.0.3",
            "info": {"title": "Regex text body", "version": "1"},
            "paths": {
                "/codes": {
                    "post": {
                        "requestBody": {
                            "required": True,
                            "content": {
                                "text/plain": {
                                    "schema": {
                                        "type": "string",
                                        "pattern": "^ID-[0-9]{3}$",
                                        "minLength": 6,
                                        "maxLength": 6,
                                    }
                                }
                            },
                        },
                        "responses": {"204": {"description": "ok"}},
                    }
                }
            },
        }
    ).operations["POST /codes"]
    config = build_initial_operation_config(operation)
    media_node_id = config.snapshot.media_type_node_ids["text/plain"]
    media_config = next(
        item
        for item in config.positive_generators
        if item.input_node_id == media_node_id
    )

    generated = generate_test_case(
        config.snapshot,
        config,
        run_seed=9,
        case_index=0,
    )

    assert config.enabled is True
    assert media_config.strategy.type == "regex"
    assert generated.media_type == "text/plain"
    assert re.fullmatch(r"ID-[0-9]{3}", generated.body) is not None


def test_enum_default_choice_generates_values_despite_conflicting_schema_constraints() -> None:
    """Scenario: verify that enum default choice generates values despite conflicting schema constraints."""
    from restscope.openapi_parser import OpenAPIParser
    from restscope.request_generation.generation import generate_test_case
    from restscope.request_generation.snapshot import build_initial_operation_config

    operation = OpenAPIParser.parse(
        {
            "openapi": "3.0.3",
            "info": {"title": "Enum authority", "version": "1"},
            "paths": {
                "/search": {
                    "get": {
                        "parameters": [
                            {
                                "name": "mode",
                                "in": "query",
                                "required": True,
                                "schema": {
                                    "type": "integer",
                                    "enum": ["fast", "thorough"],
                                    "const": 7,
                                    "minimum": 100,
                                },
                            }
                        ],
                        "responses": {"200": {"description": "ok"}},
                    }
                }
            },
        }
    ).operations["GET /search"]
    config = build_initial_operation_config(operation)

    generated = generate_test_case(
        config.snapshot,
        config,
        run_seed=7,
        case_index=0,
    )

    assert config.enabled is True
    assert generated.query_parameters["mode"] in {"fast", "thorough"}


@pytest.mark.parametrize(
    ("schema", "allowed_values"),
    [
        (
            {
                "type": "object",
                "enum": [{"name": "actual"}, {"name": "feedback"}],
                "properties": {
                    "name": {
                        "type": "string",
                        "pattern": "^[A-Z]+$",
                    }
                },
            },
            [{"name": "actual"}, {"name": "feedback"}],
        ),
        (
            {
                "type": "array",
                "enum": [["actual"], ["feedback"]],
                "items": {
                    "type": "string",
                    "pattern": "^[A-Z]+$",
                },
            },
            [["actual"], ["feedback"]],
        ),
    ],
)
def test_container_enum_choice_bypasses_descendant_generators(
    schema: dict,
    allowed_values: list,
) -> None:
    """Scenario: verify that container enum choice bypasses descendant generators."""
    from restscope.openapi_parser import OpenAPIParser
    from restscope.request_generation.generation import generate_test_case
    from restscope.request_generation.snapshot import build_initial_operation_config

    operation = OpenAPIParser.parse(
        {
            "openapi": "3.0.3",
            "info": {"title": "Container enum", "version": "1"},
            "paths": {
                "/payload": {
                    "post": {
                        "requestBody": {
                            "required": True,
                            "content": {
                                "application/json": {"schema": schema}
                            },
                        },
                        "responses": {"204": {"description": "ok"}},
                    }
                }
            },
        }
    ).operations["POST /payload"]
    config = build_initial_operation_config(operation)

    generated = generate_test_case(
        config.snapshot,
        config,
        run_seed=13,
        case_index=0,
    )

    assert config.enabled is True
    assert generated.body in allowed_values
    assert len(generated.generated_values) == 1


def test_manual_scalar_generator_overrides_the_frozen_schema_type() -> None:
    """Scenario: verify that manual scalar generator overrides the frozen schema type."""
    from restscope.openapi_parser import OpenAPIParser
    from restscope.request_generation.models import InputGeneratorConfig
    from restscope.request_generation.generation import generate_test_case
    from restscope.request_generation.snapshot import build_initial_operation_config

    operation = OpenAPIParser.parse(
        {
            "openapi": "3.0.3",
            "info": {"title": "Feedback generator", "version": "1"},
            "paths": {
                "/search": {
                    "get": {
                        "parameters": [
                            {
                                "name": "mode",
                                "in": "query",
                                "required": True,
                                "schema": {
                                    "type": "integer",
                                    "enum": [1, 2],
                                },
                            }
                        ],
                        "responses": {"200": {"description": "ok"}},
                    }
                }
            },
        }
    ).operations["GET /search"]
    initial = build_initial_operation_config(operation)
    manual = initial.model_copy(
        update={
            "positive_generators": [
                InputGeneratorConfig(
                    input_node_id=initial.positive_generators[0].input_node_id,
                    inclusion_probability=1,
                    strategy={
                        "type": "random_string",
                        "min_length": 4,
                        "max_length": 4,
                        "alphabet": "x",
                    },
                )
            ],
        }
    )

    generated = generate_test_case(
        manual.snapshot,
        manual,
        run_seed=11,
        case_index=0,
    )

    assert generated.query_parameters == {"mode": "xxxx"}


def _constrained_generation_config():
    from restscope.openapi_parser import OpenAPIParser
    from restscope.request_generation.models import InputGeneratorConfig
    from restscope.request_generation.snapshot import build_initial_operation_config

    operation = OpenAPIParser.parse(
        {
            "openapi": "3.0.3",
            "info": {"title": "Constrained generation", "version": "1"},
            "paths": {
                "/search": {
                    "post": {
                        "parameters": [
                            {
                                "name": "mode",
                                "in": "query",
                                "schema": {
                                    "type": "string",
                                    "enum": ["fast", "slow"],
                                },
                            },
                            {
                                "name": "enabled",
                                "in": "query",
                                "schema": {"type": "boolean"},
                            },
                            {
                                "name": "nullable",
                                "in": "query",
                                "schema": {
                                    "type": ["string", "null"],
                                    "enum": ["value", None],
                                },
                            },
                        ],
                        "requestBody": {
                            "required": False,
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "count": {
                                                "type": "integer",
                                                "minimum": 1,
                                                "maximum": 3,
                                            }
                                        },
                                    }
                                }
                            },
                        },
                        "responses": {"200": {"description": "ok"}},
                    }
                }
            },
        }
    ).operations["POST /search"]
    initial = build_initial_operation_config(operation)
    configs = []
    for item in initial.positive_generators:
        node = next(
            node
            for node in initial.snapshot.input_nodes
            if node.input_node_id == item.input_node_id
        )
        update = item.model_dump()
        if node.canonical_path == "query/mode":
            update["inclusion_probability"] = 1
        elif node.canonical_path == "query/enabled":
            update["inclusion_probability"] = 0.5
        elif node.canonical_path == "query/nullable":
            update["inclusion_probability"] = 1
        configs.append(InputGeneratorConfig.model_validate(update))
    return initial.model_copy(update={"positive_generators": configs})


def _node_id(config, canonical_path: str) -> str:
    return next(
        node.input_node_id
        for node in config.snapshot.input_nodes
        if node.canonical_path == canonical_path
    )


def test_generate_test_case_none_constraints_preserves_unconstrained_output() -> None:
    """Scenario: verify that generate test case none constraints preserves unconstrained output."""
    from restscope.request_generation.generation import generate_test_case

    config = _constrained_generation_config()

    existing = generate_test_case(
        config.snapshot,
        config,
        run_seed=7,
        case_index=0,
    )
    explicit_none = generate_test_case(
        config.snapshot,
        config,
        run_seed=7,
        case_index=0,
        constraints=None,
    )

    assert explicit_none == existing


def test_constrained_generation_can_force_an_optional_parameter_present() -> None:
    """Scenario: verify that constrained generation can force an optional parameter present."""
    from restscope.request_generation.constraints import ConstraintSet
    from restscope.request_generation.generation import generate_test_case

    config = _constrained_generation_config()
    mode_id = _node_id(config, "query/mode")
    enabled_id = _node_id(config, "query/enabled")
    seed = next(
        candidate
        for candidate in range(100)
        if enabled_id
        in generate_test_case(
            config.snapshot,
            config,
            run_seed=candidate,
            case_index=0,
        ).omitted_input_node_ids
    )
    constraints = ConstraintSet.model_validate(
        {
            "constraints": [
                {"type": "present", "input_node_id": mode_id},
                {
                    "type": "implies",
                    "condition": {
                        "type": "present",
                        "input_node_id": mode_id,
                    },
                    "consequence": {
                        "type": "present",
                        "input_node_id": enabled_id,
                    },
                },
            ]
        }
    )

    generated = generate_test_case(
        config.snapshot,
        config,
        run_seed=seed,
        case_index=0,
        constraints=constraints,
    )

    assert "mode" in generated.query_parameters
    assert "enabled" in generated.query_parameters
    assert enabled_id not in generated.omitted_input_node_ids


def test_constrained_generation_preserves_explicit_null_override() -> None:
    """Scenario: verify that constrained generation preserves explicit null override."""
    from restscope.request_generation.constraints import ConstraintSet
    from restscope.request_generation.generation import generate_test_case

    config = _constrained_generation_config()
    nullable_id = _node_id(config, "query/nullable")
    constraints = ConstraintSet.model_validate(
        {
            "constraints": [
                {
                    "type": "compare",
                    "operator": "==",
                    "left": {
                        "type": "input_value",
                        "input_node_id": nullable_id,
                    },
                    "right": {"type": "literal", "value": None},
                }
            ]
        }
    )

    generated = generate_test_case(
        config.snapshot,
        config,
        run_seed=3,
        case_index=0,
        constraints=constraints,
    )

    assert "nullable" in generated.query_parameters
    assert generated.query_parameters["nullable"] is None
    recorded = next(
        item
        for item in generated.generated_values
        if item.input_node_id == nullable_id
    )
    assert recorded.value is None


def test_constrained_body_property_forces_request_body_ancestors_present() -> None:
    """Scenario: verify that constrained body property forces request body ancestors present."""
    from restscope.request_generation.constraints import ConstraintSet
    from restscope.request_generation.generation import generate_test_case

    config = _constrained_generation_config()
    count_id = _node_id(
        config,
        "body/application~1json/properties/count",
    )
    body_id = _node_id(config, "body")
    constraints = ConstraintSet.model_validate(
        {
            "constraints": [
                {
                    "type": "compare",
                    "operator": "==",
                    "left": {
                        "type": "input_value",
                        "input_node_id": count_id,
                    },
                    "right": {"type": "literal", "value": 2},
                }
            ]
        }
    )

    generated = generate_test_case(
        config.snapshot,
        config,
        run_seed=5,
        case_index=0,
        constraints=constraints,
    )

    assert generated.body_present is True
    assert generated.body == {"count": 2}
    assert body_id not in generated.omitted_input_node_ids


def test_body_projection_uses_the_active_media_root_for_arrays_and_scalars() -> None:
    """Scenario: verify that body projection uses the active media root for arrays and scalars."""
    from restscope.openapi_parser import OpenAPIParser
    from restscope.request_generation.semantics import build_semantic_input_map
    from restscope.request_generation.generation import (
        generate_test_case,
        project_generated_input_value,
    )
    from restscope.request_generation.snapshot import build_initial_operation_config

    operation = OpenAPIParser.parse(
        {
            "openapi": "3.0.3",
            "info": {"title": "Body projection", "version": "1"},
            "paths": {
                "/items": {
                    "post": {
                        "requestBody": {
                            "required": True,
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "array",
                                        "minItems": 1,
                                        "maxItems": 1,
                                        "items": {
                                            "type": "object",
                                            "required": ["code"],
                                            "properties": {
                                                "code": {
                                                    "type": "string",
                                                    "enum": ["known"],
                                                }
                                            },
                                        },
                                    }
                                },
                                "text/plain": {
                                    "schema": {
                                        "type": "string",
                                        "enum": ["known"],
                                    }
                                },
                            },
                        },
                        "responses": {"200": {"description": "ok"}},
                    }
                }
            },
        }
    ).operations["POST /items"]
    json_config = build_initial_operation_config(operation)
    json_case = generate_test_case(
        json_config.snapshot,
        json_config,
        run_seed=1,
        case_index=0,
    )
    json_semantic = build_semantic_input_map(json_config)

    assert project_generated_input_value(
        json_config.snapshot,
        json_case,
        input_node_id=json_semantic.node_by_handle["body"],
    ) == [{"code": "known"}]
    assert project_generated_input_value(
        json_config.snapshot,
        json_case,
        input_node_id=json_semantic.node_by_handle["body[].code"],
    ) == ["known"]

    text_config = json_config.model_copy(
        update={"active_media_type": "text/plain"}
    )
    text_case = generate_test_case(
        text_config.snapshot,
        text_config,
        run_seed=1,
        case_index=0,
    )
    text_semantic = build_semantic_input_map(text_config)

    assert project_generated_input_value(
        text_config.snapshot,
        text_case,
        input_node_id=text_semantic.node_by_handle["body"],
    ) == "known"


def test_constrained_generation_rechecks_the_completed_case(
    monkeypatch,
) -> None:
    """Scenario: verify that constrained generation rechecks the completed case."""
    from restscope.request_generation.constraints import ConstraintSet, InputNodeOverride
    from restscope.request_generation.constraint_solver import ConstraintSolveError
    from restscope.request_generation.generation import generate_test_case

    config = _constrained_generation_config()
    mode_id = _node_id(config, "query/mode")
    constraints = ConstraintSet.model_validate(
        {
            "constraints": [
                {
                    "type": "compare",
                    "operator": "==",
                    "left": {
                        "type": "input_value",
                        "input_node_id": mode_id,
                    },
                    "right": {"type": "literal", "value": "slow"},
                }
            ]
        }
    )
    monkeypatch.setattr(
        "restscope.request_generation.constraint_solver.solve_input_overrides",
        lambda **_: {
            mode_id: InputNodeOverride(
                present=True,
                has_value=True,
                value="fast",
            )
        },
    )

    with pytest.raises(ConstraintSolveError) as raised:
        generate_test_case(
            config.snapshot,
            config,
            run_seed=5,
            case_index=0,
            constraints=constraints,
        )

    assert raised.value.code == "constraint_recheck_failed"


def test_test_case_generator_builds_configured_request_inputs_and_omits_optional_nodes() -> None:
    """Scenario: verify that test case generator builds configured request inputs and omits optional nodes."""
    from restscope.openapi_parser import OpenAPIParser
    from restscope.request_generation.models import InputGeneratorConfig, OperationGeneratorConfig
    from restscope.request_generation.generation import generate_test_case

    spec = {
        "openapi": "3.0.3",
        "info": {"title": "Generated case", "version": "1"},
        "paths": {
            "/orders/{orderId}": {
                "post": {
                    "parameters": [
                        {
                            "name": "orderId",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "integer", "minimum": 1},
                        },
                        {
                            "name": "verbose",
                            "in": "query",
                            "schema": {"type": "boolean"},
                        },
                    ],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["name", "tags"],
                                    "properties": {
                                        "name": {"type": "string", "minLength": 4, "maxLength": 4},
                                        "tags": {
                                            "type": "array",
                                            "minItems": 2,
                                            "maxItems": 2,
                                            "items": {"type": "string", "enum": ["a", "b"]},
                                        },
                                    },
                                }
                            }
                        },
                    },
                    "responses": {"201": {"description": "created"}},
                }
            }
        },
    }
    operation = OpenAPIParser.parse(spec).operations["POST /orders/{orderId}"]
    snapshot = _snapshot(operation)
    nodes = {node.canonical_path: node for node in operation.input_nodes.values()}

    def configured(path: str, probability: float, strategy: dict) -> InputGeneratorConfig:
        node = nodes[path]
        return InputGeneratorConfig(
            input_node_id=node.input_node_id,
            inclusion_probability=probability,
            strategy=strategy,
        )

    config = OperationGeneratorConfig(
        operation_key=operation.operation_key,
        snapshot=snapshot,
        active_media_type="application/json",
        positive_generators=[
            configured("path/orderId", 1, {"type": "constant", "value": 12}),
            configured("query/verbose", 0, {"type": "boolean"}),
            configured("body", 1, {"type": "request_body"}),
            configured("body/application~1json", 1, {"type": "object"}),
            configured(
                "body/application~1json/properties/name",
                1,
                {"type": "random_string", "min_length": 4, "max_length": 4, "alphabet": "xy"},
            ),
            configured(
                "body/application~1json/properties/tags",
                1,
                {"type": "array", "min_items": 2, "max_items": 2},
            ),
            configured(
                "body/application~1json/properties/tags/items",
                1,
                {"type": "choice", "values": ["a", "b"]},
            ),
        ],
    )

    generated = generate_test_case(snapshot, config, run_seed=77, case_index=0)
    repeated = generate_test_case(snapshot, config, run_seed=77, case_index=0)

    assert generated == repeated
    assert generated.path_parameters == {"orderId": 12}
    assert generated.query_parameters == {}
    assert generated.header_parameters == {}
    assert generated.cookie_parameters == {}
    assert generated.media_type == "application/json"
    assert set(generated.body) == {"name", "tags"}
    assert len(generated.body["name"]) == 4
    assert len(generated.body["tags"]) == 2
    assert set(generated.body["tags"]) <= {"a", "b"}
    assert nodes["query/verbose"].input_node_id in generated.omitted_input_node_ids
    tag_values = [
        item
        for item in generated.generated_values
        if item.input_node_id == nodes["body/application~1json/properties/tags/items"].input_node_id
    ]
    assert [item.instance_path for item in tag_values] == ["body.tags[0]", "body.tags[1]"]


def test_test_case_generator_supports_weighted_variants_and_all_of_objects() -> None:
    """Scenario: verify that test case generator supports weighted variants and all of objects."""
    from restscope.openapi_parser import OpenAPIParser
    from restscope.request_generation.models import InputGeneratorConfig, OperationGeneratorConfig
    from restscope.request_generation.generation import generate_test_case

    operation = OpenAPIParser.parse(
        {
            "openapi": "3.0.3",
            "info": {"title": "Compositions", "version": "1"},
            "paths": {
                "/subjects": {
                    "post": {
                        "requestBody": {
                            "required": True,
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "required": ["subject", "profile"],
                                        "properties": {
                                            "subject": {
                                                "oneOf": [
                                                    {
                                                        "type": "object",
                                                        "required": ["userId"],
                                                        "properties": {"userId": {"type": "integer"}},
                                                    },
                                                    {
                                                        "type": "object",
                                                        "required": ["teamId"],
                                                        "properties": {"teamId": {"type": "integer"}},
                                                    },
                                                ]
                                            },
                                            "profile": {
                                                "allOf": [
                                                    {
                                                        "type": "object",
                                                        "required": ["name"],
                                                        "properties": {"name": {"type": "string"}},
                                                    },
                                                    {
                                                        "type": "object",
                                                        "required": ["active"],
                                                        "properties": {"active": {"type": "boolean"}},
                                                    },
                                                ]
                                            },
                                        },
                                    }
                                }
                            },
                        },
                        "responses": {"201": {"description": "created"}},
                    }
                }
            },
        }
    ).operations["POST /subjects"]
    snapshot = _snapshot(operation)
    configs = []
    for node in operation.input_nodes.values():
        if node.canonical_path == "body":
            strategy = {"type": "request_body"}
        elif node.canonical_path.endswith("/subject"):
            strategy = {"type": "variant", "branch_weights": [1, 0]}
        elif node.node_kind == "array":
            strategy = {"type": "array"}
        elif node.node_kind in {"object", "media_type"}:
            strategy = {"type": "object"}
        elif node.node_kind == "variant":
            strategy = {"type": "object"}
        elif node.schema is not None and node.schema.type == "integer":
            strategy = {"type": "constant", "value": 9}
        elif node.schema is not None and node.schema.type == "boolean":
            strategy = {"type": "constant", "value": True}
        else:
            strategy = {"type": "constant", "value": "Ada"}
        configs.append(
            InputGeneratorConfig(
                input_node_id=node.input_node_id,
                inclusion_probability=1,
                strategy=strategy,
            )
        )

    generated = generate_test_case(
        snapshot,
        OperationGeneratorConfig(
            operation_key=operation.operation_key,
            snapshot=snapshot,
            active_media_type="application/json",
            positive_generators=configs,
        ),
        run_seed=1,
        case_index=0,
    )

    assert generated.body["subject"] == {"userId": 9}
    assert generated.body["profile"] == {"name": "Ada", "active": True}


def test_nullable_object_can_generate_an_explicit_json_null_body() -> None:
    """Scenario: verify that nullable object can generate an explicit json null body."""
    from restscope.openapi_parser import OpenAPIParser
    from restscope.request_generation.models import InputGeneratorConfig, OperationGeneratorConfig
    from restscope.request_generation.generation import generate_test_case
    from restscope.request_generation.serialization import serialize_test_case

    operation = OpenAPIParser.parse(
        {
            "openapi": "3.0.3",
            "info": {"title": "Nullable body", "version": "1"},
            "paths": {
                "/nullable": {
                    "post": {
                        "requestBody": {
                            "required": True,
                            "content": {
                                "application/json": {
                                    "schema": {"type": "object", "nullable": True}
                                }
                            },
                        },
                        "responses": {"200": {"description": "ok"}},
                    }
                }
            },
        }
    ).operations["POST /nullable"]
    snapshot = _snapshot(operation)
    configs = []
    for node in operation.input_nodes.values():
        strategy = (
            {"type": "request_body"}
            if node.canonical_path == "body"
            else {"type": "constant", "value": None}
        )
        configs.append(
            InputGeneratorConfig(
                input_node_id=node.input_node_id,
                inclusion_probability=1,
                strategy=strategy,
            )
        )
    case = generate_test_case(
        snapshot,
        OperationGeneratorConfig(
            operation_key=operation.operation_key,
            snapshot=snapshot,
            active_media_type="application/json",
            positive_generators=configs,
        ),
        run_seed=1,
        case_index=0,
    )

    assert case.body_present is True
    assert case.body is None
    assert case.media_type == "application/json"
    request = serialize_test_case(snapshot, case)
    assert request.content == b"null"


def test_feedback_variant_generator_does_not_revalidate_one_of_membership() -> None:
    """Scenario: verify that feedback variant generator does not revalidate one of membership."""
    from restscope.openapi_parser import OpenAPIParser
    from restscope.request_generation.models import InputGeneratorConfig, OperationGeneratorConfig
    from restscope.request_generation.generation import generate_test_case

    operation = OpenAPIParser.parse(
        {
            "openapi": "3.0.3",
            "info": {"title": "Overlapping oneOf", "version": "1"},
            "paths": {
                "/subjects": {
                    "post": {
                        "requestBody": {
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "oneOf": [
                                            {
                                                "type": "object",
                                                "required": ["id"],
                                                "properties": {"id": {"type": "integer"}},
                                            },
                                            {"type": "object"},
                                        ]
                                    }
                                }
                            }
                        },
                        "responses": {"200": {"description": "ok"}},
                    }
                }
            },
        }
    ).operations["POST /subjects"]
    snapshot = _snapshot(operation)
    configs = []
    for node in operation.input_nodes.values():
        if node.canonical_path == "body":
            strategy = {"type": "request_body"}
        elif node.canonical_path == "body/application~1json":
            strategy = {"type": "variant", "branch_weights": [1, 0]}
        elif node.node_kind == "object":
            strategy = {"type": "object"}
        else:
            strategy = {"type": "constant", "value": 3}
        configs.append(
            InputGeneratorConfig(
                input_node_id=node.input_node_id,
                inclusion_probability=1,
                strategy=strategy,
            )
        )

    generated = generate_test_case(
        snapshot,
        OperationGeneratorConfig(
            operation_key=operation.operation_key,
            snapshot=snapshot,
            active_media_type="application/json",
            positive_generators=configs,
        ),
        run_seed=1,
        case_index=0,
    )

    assert generated.body == {"id": 3}
