from __future__ import annotations

import re

import pytest


def _snapshot(operation):
    from restscope.testing.snapshot import build_operation_snapshot

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
    from restscope.testing.generation import generate_strategy_value
    from restscope.testing.models import InputGeneratorConfig

    config = InputGeneratorConfig(
        input_node_id="input_test",
        inclusion_probability=1,
        strategy=strategy,
    )

    first = generate_strategy_value(config.strategy, seed=9182)
    second = generate_strategy_value(config.strategy, seed=9182)

    assert first == second
    assert assertion(first)


def test_enum_default_choice_generates_values_despite_conflicting_schema_constraints() -> None:
    from restscope.openapi_parser import OpenAPIParser
    from restscope.testing.generation import generate_test_case
    from restscope.testing.snapshot import build_initial_operation_config

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
    from restscope.openapi_parser import OpenAPIParser
    from restscope.testing.generation import generate_test_case
    from restscope.testing.snapshot import build_initial_operation_config

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
    from restscope.openapi_parser import OpenAPIParser
    from restscope.testing import InputGeneratorConfig
    from restscope.testing.generation import generate_test_case
    from restscope.testing.snapshot import build_initial_operation_config

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
            "revision": 2,
            "configs": [
                InputGeneratorConfig(
                    input_node_id=initial.configs[0].input_node_id,
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
    from restscope.testing import InputGeneratorConfig
    from restscope.testing.snapshot import build_initial_operation_config

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
    for item in initial.configs:
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
    return initial.model_copy(update={"configs": configs})


def _node_id(config, canonical_path: str) -> str:
    return next(
        node.input_node_id
        for node in config.snapshot.input_nodes
        if node.canonical_path == canonical_path
    )


def test_generate_test_case_none_constraints_preserves_unconstrained_output() -> None:
    from restscope.testing.generation import generate_test_case

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
    from restscope.testing import ConstraintSet
    from restscope.testing.generation import generate_test_case

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
    from restscope.testing import ConstraintSet
    from restscope.testing.generation import generate_test_case

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
    from restscope.testing import ConstraintSet
    from restscope.testing.generation import generate_test_case

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


def test_constrained_generation_rechecks_the_completed_case(
    monkeypatch,
) -> None:
    from restscope.testing import ConstraintSet, InputNodeOverride
    from restscope.testing.constraint_solver import ConstraintSolveError
    from restscope.testing.generation import generate_test_case

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
        "restscope.testing.constraint_solver.solve_input_overrides",
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
    from restscope.openapi_parser import OpenAPIParser
    from restscope.testing import InputGeneratorConfig, OperationGeneratorConfig
    from restscope.testing.generation import generate_test_case

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
        revision=3,
        snapshot=snapshot,
        active_media_type="application/json",
        configs=[
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
    from restscope.openapi_parser import OpenAPIParser
    from restscope.testing import InputGeneratorConfig, OperationGeneratorConfig
    from restscope.testing.generation import generate_test_case

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
            revision=1,
            snapshot=snapshot,
            active_media_type="application/json",
            configs=configs,
        ),
        run_seed=1,
        case_index=0,
    )

    assert generated.body["subject"] == {"userId": 9}
    assert generated.body["profile"] == {"name": "Ada", "active": True}


def test_nullable_object_can_generate_an_explicit_json_null_body() -> None:
    from restscope.openapi_parser import OpenAPIParser
    from restscope.testing import InputGeneratorConfig, OperationGeneratorConfig
    from restscope.testing.generation import generate_test_case
    from restscope.testing.serialization import serialize_test_case

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
            revision=1,
            snapshot=snapshot,
            active_media_type="application/json",
            configs=configs,
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
    from restscope.openapi_parser import OpenAPIParser
    from restscope.testing import InputGeneratorConfig, OperationGeneratorConfig
    from restscope.testing.generation import generate_test_case

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
            revision=1,
            snapshot=snapshot,
            active_media_type="application/json",
            configs=configs,
        ),
        run_seed=1,
        case_index=0,
    )

    assert generated.body == {"id": 3}
