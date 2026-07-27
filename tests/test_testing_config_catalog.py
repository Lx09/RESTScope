"""Regression scenarios for testing config catalog. Each test documents one observable contract or failure boundary."""

from __future__ import annotations

from pathlib import Path

import pytest


def _ir(*, unsupported: bool = False):
    from restscope.openapi_parser import OpenAPIParser

    name_schema = (
        {"type": "string", "not": {"const": "forbidden"}}
        if unsupported
        else {"type": "string", "minLength": 2}
    )
    return OpenAPIParser.parse(
        {
            "openapi": "3.0.3",
            "info": {"title": "Testing config", "version": "1"},
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
                                "name": "trace",
                                "in": "query",
                                "schema": {"type": "string"},
                            },
                        ],
                        "requestBody": {
                            "required": True,
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "required": ["name"],
                                        "properties": {"name": name_schema},
                                    }
                                },
                                "text/plain": {"schema": {"type": "string"}},
                            },
                        },
                        "responses": {"201": {"description": "created"}},
                    }
                },
                "/status": {
                    "get": {"responses": {"200": {"description": "ok"}}}
                },
            },
        }
    )


def _catalog(tmp_path: Path):
    from restscope.db import (
        Base,
        SqlAlchemyGeneratorConfigUnitOfWork,
        create_engine_from_url,
        make_session_factory,
    )
    from restscope.testing import GeneratorConfigCatalog

    engine = create_engine_from_url(f"sqlite:///{tmp_path / 'generators.sqlite'}")
    Base.metadata.create_all(engine)
    session_factory = make_session_factory(engine)
    return (
        GeneratorConfigCatalog(
            lambda: SqlAlchemyGeneratorConfigUnitOfWork(session_factory)
        ),
        engine,
    )


def test_first_initialization_creates_every_operation_and_default_generator(tmp_path: Path) -> None:
    """Scenario: verify that first initialization creates every operation and default generator."""
    catalog, _ = _catalog(tmp_path)
    ir = _ir()

    assert catalog.initialize_once(ir) is True

    order = catalog.inspect_operation("POST /orders/{orderId}")
    status = catalog.inspect_operation("GET /status")
    assert order.revision == status.revision == 1
    assert order.active_media_type == "application/json"
    assert order.snapshot.available_media_types == [
        "application/json",
        "text/plain",
    ]
    assert {item.input_node_id for item in order.configs} == {
        item.input_node_id for item in order.snapshot.input_nodes
    }
    by_path = {
        node.canonical_path: next(
            item
            for item in order.configs
            if item.input_node_id == node.input_node_id
        )
        for node in order.snapshot.input_nodes
    }
    assert by_path["path/orderId"].inclusion_probability == 1
    assert by_path["query/trace"].inclusion_probability == 0.5
    assert by_path["body"].inclusion_probability == 1
    assert by_path["body/application~1json/properties/name"].strategy.type == (
        "random_string"
    )


def test_non_empty_enum_has_priority_over_const_default_example_and_other_constraints(
    tmp_path: Path,
) -> None:
    """Scenario: verify that non empty enum has priority over const default example and other constraints."""
    from restscope.openapi_parser import OpenAPIParser

    ir = OpenAPIParser.parse(
        {
            "openapi": "3.0.3",
            "info": {"title": "Enum priority", "version": "1"},
            "paths": {
                "/search": {
                    "get": {
                        "parameters": [
                            {
                                "name": "mode",
                                "in": "query",
                                "schema": {
                                    "type": "integer",
                                    "enum": ["fast", "thorough"],
                                    "const": 7,
                                    "default": 8,
                                    "example": 9,
                                    "minimum": 100,
                                    "pattern": "^[0-9]+$",
                                },
                            }
                        ],
                        "responses": {"200": {"description": "ok"}},
                    }
                }
            },
        }
    )
    catalog, _ = _catalog(tmp_path)

    assert catalog.initialize_once(ir) is True

    operation = catalog.inspect_operation("GET /search")
    assert operation.enabled is True
    assert operation.disabled_reasons == []
    assert len(operation.configs) == 1
    strategy = operation.configs[0].strategy
    assert strategy.type == "choice"
    assert strategy.values == ["fast", "thorough"]
    assert strategy.weights is None


def test_pattern_only_string_initializes_and_round_trips_as_regex(
    tmp_path: Path,
) -> None:
    """Scenario: a frozen string pattern becomes an enabled persisted regex strategy."""
    from restscope.openapi_parser import OpenAPIParser

    ir = OpenAPIParser.parse(
        {
            "openapi": "3.0.3",
            "info": {"title": "Regex default", "version": "1"},
            "paths": {
                "/search": {
                    "get": {
                        "parameters": [
                            {
                                "name": "code",
                                "in": "query",
                                "required": True,
                                "schema": {
                                    "type": "string",
                                    "pattern": "^[A-Z]{3}$",
                                    "minLength": 3,
                                    "maxLength": 3,
                                },
                            }
                        ],
                        "responses": {"200": {"description": "ok"}},
                    }
                }
            },
        }
    )
    catalog, _ = _catalog(tmp_path)

    assert catalog.initialize_once(ir) is True

    stored = catalog.inspect_operation("GET /search")
    assert stored.enabled is True
    assert stored.disabled_reasons == []
    assert stored.configs[0].strategy.model_dump(mode="json") == {
        "type": "regex",
        "pattern": "^[A-Z]{3}$",
        "min_length": 3,
        "max_length": 3,
    }
    assert catalog.inspect_operation("GET /search") == stored


@pytest.mark.parametrize(
    "schema",
    [
        {"type": "string", "pattern": "["},
        {
            "type": "string",
            "pattern": "^[A-Z]+$",
            "maxLength": 10_001,
        },
        {
            "type": "string",
            "pattern": "^[A-Z]+$",
            "format": "custom-code",
        },
    ],
)
def test_invalid_or_combined_regex_defaults_remain_recoverably_disabled(
    tmp_path: Path,
    schema: dict,
) -> None:
    """Scenario: unsafe or multi-contract regex defaults remain visible but disabled."""
    from restscope.openapi_parser import OpenAPIParser

    ir = OpenAPIParser.parse(
        {
            "openapi": "3.0.3",
            "info": {"title": "Unavailable regex default", "version": "1"},
            "paths": {
                "/search": {
                    "get": {
                        "parameters": [
                            {
                                "name": "code",
                                "in": "query",
                                "required": True,
                                "schema": schema,
                            }
                        ],
                        "responses": {"200": {"description": "ok"}},
                    }
                }
            },
        }
    )
    catalog, _ = _catalog(tmp_path)

    assert catalog.initialize_once(ir) is True

    stored = catalog.inspect_operation("GET /search")
    assert stored.enabled is False
    assert stored.disabled_reasons
    assert all(reason.recoverable for reason in stored.disabled_reasons)
    assert {reason.input_node_id for reason in stored.disabled_reasons} == {
        stored.configs[0].input_node_id
    }


def test_empty_enum_disables_operation_with_node_attributed_recoverable_reason(
    tmp_path: Path,
) -> None:
    """Scenario: verify that empty enum disables operation with node attributed recoverable reason."""
    from restscope.openapi_parser import OpenAPIParser

    ir = OpenAPIParser.parse(
        {
            "openapi": "3.0.3",
            "info": {"title": "Empty enum", "version": "1"},
            "paths": {
                "/search": {
                    "get": {
                        "parameters": [
                            {
                                "name": "mode",
                                "in": "query",
                                "schema": {"type": "string", "enum": []},
                            }
                        ],
                        "responses": {"200": {"description": "ok"}},
                    }
                }
            },
        }
    )
    catalog, _ = _catalog(tmp_path)

    assert catalog.initialize_once(ir) is True

    operation = catalog.inspect_operation("GET /search")
    node_id = operation.snapshot.parameters[0].input_node_id
    assert operation.enabled is False
    assert [
        (
            reason.code,
            reason.recoverable,
            reason.input_node_id,
        )
        for reason in operation.disabled_reasons
    ] == [("default_generator_unavailable", True, node_id)]


def test_body_media_priority_and_encoding_contract_are_frozen(tmp_path: Path) -> None:
    """Scenario: verify that body media priority and encoding contract are frozen."""
    from restscope.openapi_parser import OpenAPIParser

    ir = OpenAPIParser.parse(
        {
            "openapi": "3.0.3",
            "info": {"title": "Media", "version": "1"},
            "paths": {
                "/submit": {
                    "post": {
                        "requestBody": {
                            "content": {
                                "text/plain": {"schema": {"type": "string"}},
                                "application/x-www-form-urlencoded": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "name": {"type": "string"}
                                        },
                                    },
                                    "encoding": {
                                        "name": {"style": "form", "explode": False}
                                    },
                                },
                                "application/vnd.example+json": {
                                    "schema": {"type": "object"}
                                },
                                "application/json": {
                                    "schema": {"type": "object"}
                                },
                            }
                        },
                        "responses": {"204": {"description": "ok"}},
                    }
                }
            },
        }
    )
    catalog, _ = _catalog(tmp_path)
    catalog.initialize_once(ir)
    stored = catalog.inspect_operation("POST /submit")

    assert stored.snapshot.available_media_types == [
        "application/json",
        "application/vnd.example+json",
        "text/plain",
    ]
    assert stored.active_media_type == "application/json"
    assert stored.snapshot.media_type_encodings[
        "application/x-www-form-urlencoded"
    ] == {"name": {"style": "form", "explode": False}}
    assert all(
        not node.canonical_path.startswith(
            "body/application~1x-www-form-urlencoded"
        )
        for node in stored.snapshot.input_nodes
    )


def test_full_replace_can_activate_media_with_feedback_generator_constraints(
    tmp_path: Path,
) -> None:
    """Scenario: verify that full replace can activate media with feedback generator constraints."""
    from restscope.openapi_parser import OpenAPIParser

    ir = OpenAPIParser.parse(
        {
            "openapi": "3.0.3",
            "info": {"title": "Inactive media", "version": "1"},
            "paths": {
                "/submit": {
                    "post": {
                        "requestBody": {
                            "content": {
                                "application/json": {
                                    "schema": {"type": "object"}
                                },
                                "text/plain": {
                                    "schema": {
                                        "type": "string",
                                        "not": {"const": "forbidden"},
                                    }
                                },
                            }
                        },
                        "responses": {"204": {"description": "ok"}},
                    }
                }
            },
        }
    )
    catalog, _ = _catalog(tmp_path)
    catalog.initialize_once(ir)
    initial = catalog.inspect_operation("POST /submit")

    assert initial.enabled is True
    assert initial.active_media_type == "application/json"

    switched = catalog.replace_operation(
        operation_key=initial.operation_key,
        expected_revision=1,
        active_media_type="text/plain",
        configs=initial.configs,
    )
    assert switched.enabled is True
    assert switched.disabled_reasons == []
    restored = catalog.replace_operation(
        operation_key=switched.operation_key,
        expected_revision=2,
        active_media_type="application/json",
        configs=switched.configs,
    )
    assert restored.enabled is True
    assert restored.disabled_reasons == []


@pytest.mark.parametrize(
    ("media_type", "schema"),
    [
        ("text/plain", {"type": "object"}),
        ("application/x-www-form-urlencoded", {"type": "string"}),
    ],
)
def test_media_schema_incompatible_with_serializer_is_disabled(
    tmp_path: Path,
    media_type: str,
    schema: dict,
) -> None:
    """Scenario: verify that media schema incompatible with serializer is disabled."""
    from restscope.openapi_parser import OpenAPIParser

    ir = OpenAPIParser.parse(
        {
            "openapi": "3.0.3",
            "info": {"title": "Media mismatch", "version": "1"},
            "paths": {
                "/submit": {
                    "post": {
                        "requestBody": {
                            "content": {media_type: {"schema": schema}}
                        },
                        "responses": {"204": {"description": "ok"}},
                    }
                }
            },
        }
    )
    catalog, _ = _catalog(tmp_path)
    catalog.initialize_once(ir)
    stored = catalog.inspect_operation("POST /submit")

    assert stored.enabled is False
    assert stored.snapshot.available_media_types == []
    assert stored.active_media_type is None


@pytest.mark.parametrize("media_type", ["text/*", "application/*+json"])
def test_wildcard_request_media_type_is_not_frozen_as_executable(
    tmp_path: Path,
    media_type: str,
) -> None:
    """Scenario: verify that wildcard request media type is not frozen as executable."""
    from restscope.openapi_parser import OpenAPIParser

    ir = OpenAPIParser.parse(
        {
            "openapi": "3.0.3",
            "info": {"title": "Wildcard media", "version": "1"},
            "paths": {
                "/submit": {
                    "post": {
                        "requestBody": {
                            "content": {
                                media_type: {
                                    "schema": {
                                        "type": (
                                            "string"
                                            if media_type.startswith("text/")
                                            else "object"
                                        )
                                    }
                                }
                            }
                        },
                        "responses": {"204": {"description": "ok"}},
                    }
                }
            },
        }
    )
    catalog, _ = _catalog(tmp_path)
    catalog.initialize_once(ir)
    stored = catalog.inspect_operation("POST /submit")

    assert stored.enabled is False
    assert stored.snapshot.available_media_types == []
    assert stored.active_media_type is None


@pytest.mark.parametrize(
    ("schema", "explode"),
    [
        ({"type": "string"}, True),
        ({"type": "object", "properties": {"name": {"type": "string"}}}, None),
    ],
)
def test_unserializable_deep_object_parameter_disables_operation(
    tmp_path: Path,
    schema: dict,
    explode: bool | None,
) -> None:
    """Scenario: verify that unserializable deep object parameter disables operation."""
    from restscope.openapi_parser import OpenAPIParser

    parameter = {
        "name": "filter",
        "in": "query",
        "style": "deepObject",
        "schema": schema,
    }
    if explode is not None:
        parameter["explode"] = explode
    ir = OpenAPIParser.parse(
        {
            "openapi": "3.0.3",
            "info": {"title": "Deep object", "version": "1"},
            "paths": {
                "/search": {
                    "get": {
                        "parameters": [parameter],
                        "responses": {"200": {"description": "ok"}},
                    }
                }
            },
        }
    )
    catalog, _ = _catalog(tmp_path)
    catalog.initialize_once(ir)
    stored = catalog.inspect_operation("GET /search")

    assert stored.enabled is False
    assert {
        reason.code for reason in stored.disabled_reasons
    } == {"request_parameter_unsupported"}


def test_read_only_required_property_is_not_frozen_as_a_request_input(
    tmp_path: Path,
) -> None:
    """Scenario: verify that read only required property is not frozen as a request input."""
    from restscope.openapi_parser import OpenAPIParser

    ir = OpenAPIParser.parse(
        {
            "openapi": "3.0.3",
            "info": {"title": "Read only", "version": "1"},
            "paths": {
                "/users": {
                    "post": {
                        "requestBody": {
                            "required": True,
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "required": ["id", "name"],
                                        "properties": {
                                            "id": {
                                                "type": "integer",
                                                "readOnly": True,
                                            },
                                            "name": {"type": "string"},
                                        },
                                        "example": {"id": 7, "name": "Ada"},
                                    }
                                }
                            },
                        },
                        "responses": {"201": {"description": "created"}},
                    }
                }
            },
        }
    )
    catalog, _ = _catalog(tmp_path)
    catalog.initialize_once(ir)
    stored = catalog.inspect_operation("POST /users")
    paths = {node.canonical_path for node in stored.snapshot.input_nodes}
    media_node = next(
        node
        for node in stored.snapshot.input_nodes
        if node.canonical_path == "body/application~1json"
    )

    assert "body/application~1json/properties/id" not in paths
    assert media_node.schema_contract.required == ["name"]
    assert set(media_node.schema_contract.properties) == {"name"}
    media_config = next(
        item
        for item in stored.configs
        if item.input_node_id == media_node.input_node_id
    )
    assert media_config.strategy.value == {"name": "Ada"}

    from restscope.testing import InputGeneratorConfig

    invalid = [
        InputGeneratorConfig.model_validate(
            {
                **item.model_dump(mode="json"),
                "strategy": {
                    "type": "constant",
                    "value": {"id": 9, "name": "Grace"},
                },
            }
        )
        if item.input_node_id == media_node.input_node_id
        else item
        for item in stored.configs
    ]
    replaced = catalog.replace_operation(
        operation_key=stored.operation_key,
        expected_revision=1,
        active_media_type="application/json",
        configs=invalid,
    )
    assert replaced.enabled is True
    replaced_media_config = next(
        item
        for item in replaced.configs
        if item.input_node_id == media_node.input_node_id
    )
    assert replaced_media_config.strategy.value == {
        "id": 9,
        "name": "Grace",
    }


def test_replace_and_patch_use_revision_lock_and_preserve_snapshot(tmp_path: Path) -> None:
    """Scenario: verify that replace and patch use revision lock and preserve snapshot."""
    from restscope.testing import GeneratorConfigRevisionConflict

    catalog, _ = _catalog(tmp_path)
    catalog.initialize_once(_ir())
    original = catalog.inspect_operation("POST /orders/{orderId}")
    trace = next(
        item
        for item in original.configs
        if next(
            node
            for node in original.snapshot.input_nodes
            if node.input_node_id == item.input_node_id
        ).canonical_path
        == "query/trace"
    )

    patched = catalog.patch_operation(
        operation_key=original.operation_key,
        expected_revision=1,
        updates=[
            {
                "input_node_id": trace.input_node_id,
                "inclusion_probability": 1,
                "strategy": {"type": "constant", "value": "trace-value"},
            }
        ],
    )
    assert patched.revision == 2
    assert patched.snapshot == original.snapshot
    assert next(
        item for item in patched.configs if item.input_node_id == trace.input_node_id
    ).strategy.value == "trace-value"

    replaced = catalog.replace_operation(
        operation_key=original.operation_key,
        expected_revision=2,
        active_media_type="text/plain",
        configs=patched.configs,
    )
    assert replaced.revision == 3
    assert replaced.active_media_type == "text/plain"
    with pytest.raises(GeneratorConfigRevisionConflict):
        catalog.replace_operation(
            operation_key=original.operation_key,
            expected_revision=2,
            active_media_type="application/json",
            configs=replaced.configs,
        )


def test_replace_requires_complete_frozen_node_set_and_required_inclusion(tmp_path: Path) -> None:
    """Scenario: verify that replace requires complete frozen node set and required inclusion."""
    from restscope.testing import GeneratorConfigError

    catalog, _ = _catalog(tmp_path)
    catalog.initialize_once(_ir())
    current = catalog.inspect_operation("POST /orders/{orderId}")

    with pytest.raises(GeneratorConfigError) as missing:
        catalog.replace_operation(
            operation_key=current.operation_key,
            expected_revision=1,
            active_media_type=current.active_media_type,
            configs=current.configs[:-1],
        )
    assert missing.value.code == "generator_config_incomplete"

    required_id = next(
        node.input_node_id
        for node in current.snapshot.input_nodes
        if node.canonical_path == "path/orderId"
    )
    invalid = [
        item.model_copy(update={"inclusion_probability": 0.5})
        if item.input_node_id == required_id
        else item
        for item in current.configs
    ]
    with pytest.raises(GeneratorConfigError) as inclusion:
        catalog.replace_operation(
            operation_key=current.operation_key,
            expected_revision=1,
            active_media_type=current.active_media_type,
            configs=invalid,
        )
    assert inclusion.value.code == "generator_config_invalid_inclusion"


def test_structural_generator_strategy_must_match_frozen_node(tmp_path: Path) -> None:
    """Scenario: verify that structural generator strategy must match frozen node."""
    from restscope.testing import GeneratorConfigError

    catalog, _ = _catalog(tmp_path)
    catalog.initialize_once(_ir())
    current = catalog.inspect_operation("POST /orders/{orderId}")
    body_id = current.snapshot.request_body_node_id

    with pytest.raises(GeneratorConfigError) as raised:
        catalog.patch_operation(
            operation_key=current.operation_key,
            expected_revision=1,
            updates=[
                {
                    "input_node_id": body_id,
                    "strategy": {"type": "constant", "value": "invalid"},
                }
            ],
        )
    assert raised.value.code == "generator_config_incompatible_strategy"


def test_unsupported_operation_is_saved_disabled_without_blocking_other_operations(
    tmp_path: Path,
) -> None:
    """Scenario: verify that unsupported operation is saved disabled without blocking other operations."""
    from restscope.testing import GeneratorConfigError

    catalog, _ = _catalog(tmp_path)
    assert catalog.initialize_once(_ir(unsupported=True)) is True

    disabled = catalog.inspect_operation("POST /orders/{orderId}")
    assert disabled.enabled is False
    assert {reason.code for reason in disabled.disabled_reasons} == {
        "request_schema_unsupported"
    }
    assert catalog.inspect_operation("GET /status").enabled is True
    with pytest.raises(GeneratorConfigError) as raised:
        catalog.require_operation(disabled.operation_key)
    assert raised.value.code == "generator_operation_unsupported"
    replaced = catalog.replace_operation(
        operation_key=disabled.operation_key,
        expected_revision=1,
        active_media_type=disabled.active_media_type,
        configs=disabled.configs,
    )
    assert replaced.enabled is True
    assert replaced.disabled_reasons == []
    assert catalog.require_operation(replaced.operation_key).revision == 2


def test_default_derivation_failure_disables_only_that_operation(
    tmp_path: Path,
) -> None:
    """Scenario: verify that default derivation failure disables only that operation."""
    from restscope.openapi_parser import OpenAPIParser

    ir = OpenAPIParser.parse(
        {
            "openapi": "3.0.3",
            "info": {"title": "Invalid bounds", "version": "1"},
            "paths": {
                "/bad": {
                    "get": {
                        "parameters": [
                            {
                                "name": "value",
                                "in": "query",
                                "schema": {
                                    "type": "string",
                                    "minLength": 5,
                                    "maxLength": 2,
                                },
                            }
                        ],
                        "responses": {"200": {"description": "ok"}},
                    }
                },
                "/good": {
                    "get": {"responses": {"200": {"description": "ok"}}}
                },
            },
        }
    )
    catalog, _ = _catalog(tmp_path)

    assert catalog.initialize_once(ir) is True
    assert catalog.inspect_operation("GET /bad").enabled is False
    assert catalog.inspect_operation("GET /good").enabled is True


def test_json_schema_nullable_type_array_builds_a_valid_default(
    tmp_path: Path,
) -> None:
    """Scenario: verify that json schema nullable type array builds a valid default."""
    from restscope.openapi_parser import OpenAPIParser

    ir = OpenAPIParser.parse(
        {
            "openapi": "3.1.0",
            "info": {"title": "Nullable type", "version": "1"},
            "paths": {
                "/search": {
                    "get": {
                        "parameters": [
                            {
                                "name": "query",
                                "in": "query",
                                "schema": {"type": ["string", "null"]},
                            }
                        ],
                        "responses": {"200": {"description": "ok"}},
                    }
                }
            },
        }
    )
    catalog, _ = _catalog(tmp_path)

    assert catalog.initialize_once(ir) is True
    stored = catalog.inspect_operation("GET /search")
    assert stored.enabled is True
    assert stored.configs[0].strategy.type == "random_string"


def test_combiner_with_sibling_constraints_is_saved_as_unsupported(
    tmp_path: Path,
) -> None:
    """Scenario: verify that combiner with sibling constraints is saved as unsupported."""
    from restscope.openapi_parser import OpenAPIParser

    ir = OpenAPIParser.parse(
        {
            "openapi": "3.0.3",
            "info": {"title": "Combiner siblings", "version": "1"},
            "paths": {
                "/mixed": {
                    "post": {
                        "requestBody": {
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "properties": {
                                            "common": {"type": "string"}
                                        },
                                        "required": ["common"],
                                        "oneOf": [
                                            {"type": "string"},
                                            {"type": "integer"},
                                        ],
                                    }
                                }
                            }
                        },
                        "responses": {"200": {"description": "ok"}},
                    }
                }
            },
        }
    )
    catalog, _ = _catalog(tmp_path)
    catalog.initialize_once(ir)

    stored = catalog.inspect_operation("POST /mixed")

    assert stored.enabled is False
    assert "request_schema_unsupported" in {
        reason.code for reason in stored.disabled_reasons
    }


def test_object_cardinality_requires_a_generator_set_that_always_conforms(
    tmp_path: Path,
) -> None:
    """Scenario: verify that object cardinality requires a generator set that always conforms."""
    from restscope.openapi_parser import OpenAPIParser
    from restscope.testing import InputGeneratorConfig

    ir = OpenAPIParser.parse(
        {
            "openapi": "3.0.3",
            "info": {"title": "Object cardinality", "version": "1"},
            "paths": {
                "/objects": {
                    "post": {
                        "requestBody": {
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "minProperties": 1,
                                        "properties": {
                                            "optional": {"type": "string"}
                                        },
                                    }
                                }
                            }
                        },
                        "responses": {"200": {"description": "ok"}},
                    }
                }
            },
        }
    )
    catalog, _ = _catalog(tmp_path)
    catalog.initialize_once(ir)
    initial = catalog.inspect_operation("POST /objects")
    assert initial.enabled is False
    parent_id = initial.snapshot.media_type_node_ids["application/json"]
    assert {
        reason.input_node_id
        for reason in initial.disabled_reasons
        if reason.recoverable
    } == {parent_id}

    property_id = next(
        node.input_node_id
        for node in initial.snapshot.input_nodes
        if node.canonical_path.endswith("/properties/optional")
    )
    configs = [
        InputGeneratorConfig.model_validate(
            {
                **item.model_dump(mode="json"),
                "inclusion_probability": 1,
            }
        )
        if item.input_node_id == property_id
        else item
        for item in initial.configs
    ]
    replaced = catalog.replace_operation(
        operation_key=initial.operation_key,
        expected_revision=1,
        active_media_type="application/json",
        configs=configs,
    )
    assert replaced.enabled is True


def test_constant_object_replace_ignores_unused_child_inclusion_probabilities(
    tmp_path: Path,
) -> None:
    """Scenario: verify that constant object replace ignores unused child inclusion probabilities."""
    from restscope.openapi_parser import OpenAPIParser
    from restscope.testing import InputGeneratorConfig

    ir = OpenAPIParser.parse(
        {
            "openapi": "3.0.3",
            "info": {"title": "Constant object", "version": "1"},
            "paths": {
                "/objects": {
                    "post": {
                        "requestBody": {
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "minProperties": 1,
                                        "properties": {
                                            "optional": {"type": "string"}
                                        },
                                    }
                                }
                            }
                        },
                        "responses": {"200": {"description": "ok"}},
                    }
                }
            },
        }
    )
    catalog, _ = _catalog(tmp_path)
    catalog.initialize_once(ir)
    initial = catalog.inspect_operation("POST /objects")
    parent_id = initial.snapshot.media_type_node_ids["application/json"]
    configs = [
        InputGeneratorConfig.model_validate(
            {
                **item.model_dump(mode="json"),
                "strategy": {
                    "type": "constant",
                    "value": {"optional": "fixed"},
                },
            }
        )
        if item.input_node_id == parent_id
        else item
        for item in initial.configs
    ]

    replaced = catalog.replace_operation(
        operation_key=initial.operation_key,
        expected_revision=1,
        active_media_type="application/json",
        configs=configs,
    )

    assert replaced.enabled is True


def test_nullable_text_body_rejects_a_null_generator_before_execution(
    tmp_path: Path,
) -> None:
    """Scenario: verify that nullable text body rejects a null generator before execution."""
    from restscope.openapi_parser import OpenAPIParser
    from restscope.testing import GeneratorConfigError, InputGeneratorConfig

    ir = OpenAPIParser.parse(
        {
            "openapi": "3.1.0",
            "info": {"title": "Nullable text", "version": "1"},
            "paths": {
                "/text": {
                    "post": {
                        "requestBody": {
                            "content": {
                                "text/plain": {
                                    "schema": {
                                        "type": ["string", "null"]
                                    }
                                }
                            }
                        },
                        "responses": {"204": {"description": "ok"}},
                    }
                }
            },
        }
    )
    catalog, _ = _catalog(tmp_path)
    catalog.initialize_once(ir)
    initial = catalog.inspect_operation("POST /text")
    media_id = initial.snapshot.media_type_node_ids["text/plain"]
    configs = [
        InputGeneratorConfig.model_validate(
            {
                **item.model_dump(mode="json"),
                "strategy": {"type": "constant", "value": None},
            }
        )
        if item.input_node_id == media_id
        else item
        for item in initial.configs
    ]

    with pytest.raises(GeneratorConfigError) as raised:
        catalog.replace_operation(
            operation_key=initial.operation_key,
            expected_revision=1,
            active_media_type="text/plain",
            configs=configs,
        )

    assert raised.value.code == "generator_config_incompatible_strategy"


@pytest.mark.parametrize(
    "strategy",
    [
        {"type": "integer_range", "minimum": 1, "maximum": 3},
        {"type": "number_range", "minimum": 1.0, "maximum": 3.0},
    ],
)
def test_manual_range_generator_can_override_a_discrete_enum(
    tmp_path: Path,
    strategy: dict,
) -> None:
    """Scenario: verify that manual range generator can override a discrete enum."""
    from restscope.openapi_parser import OpenAPIParser
    from restscope.testing import InputGeneratorConfig

    schema_type = "integer" if strategy["type"] == "integer_range" else "number"
    ir = OpenAPIParser.parse(
        {
            "openapi": "3.0.3",
            "info": {"title": "Discrete enum", "version": "1"},
            "paths": {
                "/search": {
                    "get": {
                        "parameters": [
                            {
                                "name": "value",
                                "in": "query",
                                "schema": {
                                    "type": schema_type,
                                    "enum": [1, 3],
                                },
                            }
                        ],
                        "responses": {"200": {"description": "ok"}},
                    }
                }
            },
        }
    )
    catalog, _ = _catalog(tmp_path)
    catalog.initialize_once(ir)
    initial = catalog.inspect_operation("GET /search")
    configs = [
        InputGeneratorConfig.model_validate(
            {
                **item.model_dump(mode="json"),
                "strategy": strategy,
            }
        )
        for item in initial.configs
    ]

    replaced = catalog.replace_operation(
        operation_key=initial.operation_key,
        expected_revision=1,
        active_media_type=None,
        configs=configs,
    )

    assert replaced.enabled is True
    assert replaced.configs[0].strategy.type == strategy["type"]


def test_patch_with_feedback_generator_clears_its_recoverable_reason(
    tmp_path: Path,
) -> None:
    """Scenario: verify that patch with feedback generator clears its recoverable reason."""
    from restscope.openapi_parser import OpenAPIParser

    ir = OpenAPIParser.parse(
        {
            "openapi": "3.0.3",
            "info": {"title": "Explicit defaults", "version": "1"},
            "paths": {
                "/search": {
                    "get": {
                        "parameters": [
                            {
                                "name": "code",
                                "in": "query",
                                "schema": {
                                    "type": "string",
                                    "pattern": "^[A-Z]{3}$",
                                    "example": "invalid",
                                },
                            }
                        ],
                        "responses": {"200": {"description": "ok"}},
                    }
                }
            },
        }
    )
    catalog, _ = _catalog(tmp_path)
    catalog.initialize_once(ir)
    initial = catalog.inspect_operation("GET /search")
    assert initial.enabled is False
    assert {reason.code for reason in initial.disabled_reasons} == {
        "default_generator_unavailable"
    }
    node_id = initial.configs[0].input_node_id
    assert {reason.input_node_id for reason in initial.disabled_reasons} == {
        node_id
    }

    patched = catalog.patch_operation(
        operation_key=initial.operation_key,
        expected_revision=1,
        updates=[
            {
                "input_node_id": node_id,
                "strategy": {"type": "constant", "value": "lowercase"},
            }
        ],
    )
    assert patched.enabled is True
    assert patched.disabled_reasons == []
    assert patched.configs[0].strategy.value == "lowercase"


def test_patch_clears_only_recoverable_reasons_for_updated_nodes(
    tmp_path: Path,
) -> None:
    """Scenario: verify that patch clears only recoverable reasons for updated nodes."""
    from restscope.openapi_parser import OpenAPIParser

    ir = OpenAPIParser.parse(
        {
            "openapi": "3.0.3",
            "info": {"title": "Patch recovery", "version": "1"},
            "paths": {
                "/search": {
                    "get": {
                        "parameters": [
                            {
                                "name": name,
                                "in": "query",
                                "schema": {
                                    "type": "string",
                                    "pattern": "^[A-Z]+$",
                                    "format": "custom-code",
                                },
                            }
                            for name in ("first", "second")
                        ],
                        "responses": {"200": {"description": "ok"}},
                    }
                }
            },
        }
    )
    catalog, _ = _catalog(tmp_path)
    catalog.initialize_once(ir)
    initial = catalog.inspect_operation("GET /search")
    node_ids = {
        parameter.name: parameter.input_node_id
        for parameter in initial.snapshot.parameters
    }
    assert {
        reason.input_node_id
        for reason in initial.disabled_reasons
        if reason.recoverable
    } == set(node_ids.values())

    first = catalog.patch_operation(
        operation_key=initial.operation_key,
        expected_revision=1,
        updates=[
            {
                "input_node_id": node_ids["first"],
                "strategy": {"type": "constant", "value": "feedback-first"},
            }
        ],
    )
    assert first.enabled is False
    assert {
        reason.input_node_id
        for reason in first.disabled_reasons
        if reason.recoverable
    } == {node_ids["second"]}

    second = catalog.patch_operation(
        operation_key=first.operation_key,
        expected_revision=2,
        updates=[
            {
                "input_node_id": node_ids["second"],
                "strategy": {"type": "constant", "value": "feedback-second"},
            }
        ],
    )
    assert second.enabled is True
    assert second.disabled_reasons == []


def test_patch_does_not_hide_an_unrelated_default_configuration_failure(
    tmp_path: Path,
) -> None:
    """Scenario: verify that patch does not hide an unrelated default configuration failure."""
    from restscope.openapi_parser import OpenAPIParser

    ir = OpenAPIParser.parse(
        {
            "openapi": "3.0.3",
            "info": {"title": "Independent recovery reasons", "version": "1"},
            "paths": {
                "/search": {
                    "post": {
                        "parameters": [
                            {
                                "name": "code",
                                "in": "query",
                                "schema": {
                                    "type": "string",
                                    "pattern": "^[A-Z]+$",
                                    "format": "custom-code",
                                },
                            }
                        ],
                        "requestBody": {
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "minProperties": 1,
                                        "properties": {
                                            "optional": {"type": "string"}
                                        },
                                    }
                                }
                            }
                        },
                        "responses": {"200": {"description": "ok"}},
                    }
                }
            },
        }
    )
    catalog, _ = _catalog(tmp_path)
    catalog.initialize_once(ir)
    initial = catalog.inspect_operation("POST /search")
    query_id = initial.snapshot.parameters[0].input_node_id
    body_id = initial.snapshot.media_type_node_ids["application/json"]

    assert {
        reason.input_node_id
        for reason in initial.disabled_reasons
        if reason.recoverable
    } == {query_id, body_id}

    patched = catalog.patch_operation(
        operation_key=initial.operation_key,
        expected_revision=1,
        updates=[
            {
                "input_node_id": query_id,
                "strategy": {"type": "constant", "value": "feedback"},
            }
        ],
    )

    assert patched.enabled is False
    assert {
        reason.input_node_id
        for reason in patched.disabled_reasons
        if reason.recoverable
    } == {body_id}


def test_catalog_initialization_rolls_back_all_records_and_can_retry(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Scenario: verify that catalog initialization rolls back all records and can retry."""
    from restscope.db.repositories import SqlAlchemyGeneratorConfigRepository

    catalog, _ = _catalog(tmp_path)
    original_initialize = SqlAlchemyGeneratorConfigRepository.initialize

    def fail_after_flush(self, records):
        original_initialize(self, records)
        raise RuntimeError("simulated initialization failure")

    monkeypatch.setattr(
        SqlAlchemyGeneratorConfigRepository,
        "initialize",
        fail_after_flush,
    )
    with pytest.raises(RuntimeError, match="simulated initialization failure"):
        catalog.initialize_once(_ir())
    assert catalog.get_operation("POST /orders/{orderId}") is None
    assert catalog.get_operation("GET /status") is None

    monkeypatch.setattr(
        SqlAlchemyGeneratorConfigRepository,
        "initialize",
        original_initialize,
    )
    assert catalog.initialize_once(_ir()) is True


def test_repository_compare_and_swap_rejects_a_concurrent_old_revision(
    tmp_path: Path,
) -> None:
    """Scenario: verify that repository compare and swap rejects a concurrent old revision."""
    from restscope.db import SqlAlchemyGeneratorConfigUnitOfWork, make_session_factory
    from restscope.testing.ports import GeneratorConfigConcurrentWrite

    catalog, engine = _catalog(tmp_path)
    catalog.initialize_once(_ir())
    created = catalog.inspect_operation("POST /orders/{orderId}")
    session_factory = make_session_factory(engine)

    with (
        SqlAlchemyGeneratorConfigUnitOfWork(session_factory) as first,
        SqlAlchemyGeneratorConfigUnitOfWork(session_factory) as second,
    ):
        assert first.generator_configs.get(created.operation_key).revision == 1
        assert second.generator_configs.get(created.operation_key).revision == 1
        payload = {
            "operation_key": created.operation_key,
            "expected_revision": 1,
            "revision": 2,
            "snapshot": created.snapshot.model_dump(mode="json"),
            "enabled": created.enabled,
            "disabled_reasons": [
                item.model_dump(mode="json") for item in created.disabled_reasons
            ],
            "active_media_type": created.active_media_type,
            "configs": created.configs,
        }
        first.generator_configs.replace(**payload)
        first.commit()

        with pytest.raises(GeneratorConfigConcurrentWrite):
            second.generator_configs.replace(**payload)


def test_generator_catalog_has_no_delete_operation(tmp_path: Path) -> None:
    """Scenario: verify that generator catalog has no delete operation."""
    catalog, _ = _catalog(tmp_path)

    assert not hasattr(catalog, "delete_operation")
