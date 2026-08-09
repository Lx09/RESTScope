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
    from restscope.request_generation import RequestGenerationConfigStore

    engine = create_engine_from_url(f"sqlite:///{tmp_path / 'generators.sqlite'}")
    Base.metadata.create_all(engine)
    session_factory = make_session_factory(engine)
    return (
        RequestGenerationConfigStore(
            lambda: SqlAlchemyGeneratorConfigUnitOfWork(session_factory)
        ),
        engine,
    )


def _apply_accepted_patch(catalog, operation_key: str, updates):
    """Apply test setup through the current-content repository boundary."""
    from restscope.request_generation import prepare_accepted_generator_patch

    current = catalog.get_operation(operation_key)
    assert current is not None
    updated = prepare_accepted_generator_patch(current, updates)
    with catalog.unit_of_work_factory() as uow:
        uow.generator_configs.replace_inputs(
            operation_key=operation_key,
            expected=current.configs,
            updated=updated.configs,
        )
        uow.commit()
    return catalog.get_operation(operation_key)


def _apply_accepted_configs(catalog, current, configs):
    """Translate changed full configs into the accepted Patch input boundary."""
    previous = {item.input_node_id: item for item in current.configs}
    updates = []
    for item in configs:
        before = previous[item.input_node_id]
        update = {"input_node_id": item.input_node_id}
        if item.inclusion_probability != before.inclusion_probability:
            update["inclusion_probability"] = item.inclusion_probability
        if item.strategy != before.strategy:
            update["strategy"] = item.strategy
        if len(update) > 1:
            updates.append(update)
    return _apply_accepted_patch(catalog, current.operation_key, updates)


def test_first_initialization_creates_every_operation_and_default_generator(tmp_path: Path) -> None:
    """Scenario: verify that first initialization creates every operation and default generator."""
    catalog, _ = _catalog(tmp_path)
    ir = _ir()

    assert catalog.initialize_once(ir) is True

    order = catalog.get_operation("POST /orders/{orderId}")
    status = catalog.get_operation("GET /status")
    assert order is not None
    assert status is not None
    assert not hasattr(order, "revision")
    assert not hasattr(status, "revision")
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

    operation = catalog.get_operation("GET /search")
    assert operation is not None
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

    stored = catalog.get_operation("GET /search")
    assert stored is not None
    assert stored.enabled is True
    assert stored.disabled_reasons == []
    assert stored.configs[0].strategy.model_dump(mode="json") == {
        "type": "regex",
        "pattern": "^[A-Z]{3}$",
        "min_length": 3,
        "max_length": 3,
    }
    assert catalog.get_operation("GET /search") == stored


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

    stored = catalog.get_operation("GET /search")
    assert stored is not None
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

    operation = catalog.get_operation("GET /search")
    assert operation is not None
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
    stored = catalog.get_operation("POST /submit")
    assert stored is not None

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


def test_non_file_multipart_is_enabled_and_omits_optional_binary_inputs(
    tmp_path: Path,
) -> None:
    """A GitLab-style multipart object remains executable without file fields."""
    from restscope.openapi_parser import OpenAPIParser

    ir = OpenAPIParser.parse(
        {
            "openapi": "3.0.3",
            "info": {"title": "Multipart project", "version": "1"},
            "paths": {
                "/projects": {
                    "post": {
                        "requestBody": {
                            "required": True,
                            "content": {
                                "multipart/form-data": {
                                    "schema": {
                                        "type": "object",
                                        "required": ["name"],
                                        "properties": {
                                            "name": {
                                                "type": "string",
                                                "minLength": 3,
                                            },
                                            "avatar": {
                                                "type": "string",
                                                "format": "binary",
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
    )
    catalog, _ = _catalog(tmp_path)

    catalog.initialize_once(ir)
    stored = catalog.get_operation("POST /projects")

    assert stored is not None
    assert stored.enabled is True
    assert stored.active_media_type == "multipart/form-data"
    paths = {node.canonical_path for node in stored.snapshot.input_nodes}
    assert "body/multipart~1form-data/properties/name" in paths
    assert "body/multipart~1form-data/properties/avatar" not in paths
    assert {
        item.input_node_id for item in stored.configs
    } == {
        node.input_node_id for node in stored.snapshot.input_nodes
    }


@pytest.mark.parametrize(
    ("schema", "encoding"),
    [
        (
            {
                "type": "object",
                "required": ["avatar"],
                "properties": {
                    "avatar": {"type": "string", "format": "binary"}
                },
            },
            None,
        ),
        ({"type": "string"}, None),
        (
            {
                "type": "object",
                "properties": {"name": {"type": "string"}},
            },
            {"name": {"contentType": "text/plain"}},
        ),
    ],
)
def test_multipart_rejects_required_files_non_objects_and_explicit_encoding(
    tmp_path: Path,
    schema: dict,
    encoding: dict | None,
) -> None:
    """The narrow multipart capability fails closed outside non-file objects."""
    from restscope.openapi_parser import OpenAPIParser

    media: dict = {"schema": schema}
    if encoding is not None:
        media["encoding"] = encoding
    ir = OpenAPIParser.parse(
        {
            "openapi": "3.0.3",
            "info": {"title": "Unsupported multipart", "version": "1"},
            "paths": {
                "/upload": {
                    "post": {
                        "requestBody": {
                            "content": {"multipart/form-data": media}
                        },
                        "responses": {"204": {"description": "ok"}},
                    }
                }
            },
        }
    )
    catalog, _ = _catalog(tmp_path)

    catalog.initialize_once(ir)
    stored = catalog.get_operation("POST /upload")

    assert stored is not None
    assert stored.enabled is False
    assert stored.active_media_type is None
    assert stored.snapshot.available_media_types == []


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
    stored = catalog.get_operation("POST /submit")
    assert stored is not None

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
    stored = catalog.get_operation("POST /submit")
    assert stored is not None

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
    stored = catalog.get_operation("GET /search")
    assert stored is not None

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
    stored = catalog.get_operation("POST /users")
    assert stored is not None
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

    from restscope.request_generation import InputGeneratorConfig

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
    replaced = _apply_accepted_configs(catalog, stored, invalid)
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


def test_structural_generator_strategy_must_match_frozen_node(tmp_path: Path) -> None:
    """Scenario: verify that structural generator strategy must match frozen node."""
    from restscope.request_generation import GeneratorConfigError

    catalog, _ = _catalog(tmp_path)
    catalog.initialize_once(_ir())
    current = catalog.get_operation("POST /orders/{orderId}")
    assert current is not None
    body_id = current.snapshot.request_body_node_id

    with pytest.raises(GeneratorConfigError) as raised:
        _apply_accepted_patch(
            catalog,
            current.operation_key,
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
    from restscope.request_generation import GeneratorConfigError

    catalog, _ = _catalog(tmp_path)
    assert catalog.initialize_once(_ir(unsupported=True)) is True

    disabled = catalog.get_operation("POST /orders/{orderId}")
    assert disabled is not None
    assert disabled.enabled is False
    assert {reason.code for reason in disabled.disabled_reasons} == {
        "request_schema_unsupported"
    }
    status = catalog.get_operation("GET /status")
    assert status is not None
    assert status.enabled is True
    with pytest.raises(GeneratorConfigError) as raised:
        catalog.require_operation(disabled.operation_key)
    assert raised.value.code == "generator_operation_unsupported"
    disabled_node_id = next(
        reason.input_node_id
        for reason in disabled.disabled_reasons
        if reason.input_node_id is not None
    )
    replaced = _apply_accepted_patch(
        catalog,
        disabled.operation_key,
        updates=[
            {
                "input_node_id": disabled_node_id,
                "strategy": {"type": "constant", "value": "supported"},
            }
        ],
    )
    assert replaced.enabled is True
    assert replaced.disabled_reasons == []
    assert catalog.require_operation(replaced.operation_key) == replaced


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
    bad = catalog.get_operation("GET /bad")
    good = catalog.get_operation("GET /good")
    assert bad is not None
    assert good is not None
    assert bad.enabled is False
    assert good.enabled is True


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
    stored = catalog.get_operation("GET /search")
    assert stored is not None
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

    stored = catalog.get_operation("POST /mixed")
    assert stored is not None

    assert stored.enabled is False
    assert "request_schema_unsupported" in {
        reason.code for reason in stored.disabled_reasons
    }


def test_object_cardinality_requires_a_generator_set_that_always_conforms(
    tmp_path: Path,
) -> None:
    """Scenario: verify that object cardinality requires a generator set that always conforms."""
    from restscope.openapi_parser import OpenAPIParser
    from restscope.request_generation import InputGeneratorConfig

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
    initial = catalog.get_operation("POST /objects")
    assert initial is not None
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
    replaced = _apply_accepted_configs(catalog, initial, configs)
    assert replaced.enabled is True


def test_constant_object_patch_ignores_unused_child_inclusion_probabilities(
    tmp_path: Path,
) -> None:
    """A constant-object Patch need not rewrite unused child probabilities."""
    from restscope.openapi_parser import OpenAPIParser
    from restscope.request_generation import InputGeneratorConfig

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
    initial = catalog.get_operation("POST /objects")
    assert initial is not None
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

    replaced = _apply_accepted_configs(catalog, initial, configs)

    assert replaced.enabled is True


def test_nullable_text_body_rejects_a_null_generator_before_execution(
    tmp_path: Path,
) -> None:
    """Scenario: verify that nullable text body rejects a null generator before execution."""
    from restscope.openapi_parser import OpenAPIParser
    from restscope.request_generation import GeneratorConfigError, InputGeneratorConfig

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
    initial = catalog.get_operation("POST /text")
    assert initial is not None
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
        _apply_accepted_configs(catalog, initial, configs)

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
    from restscope.request_generation import InputGeneratorConfig

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
    initial = catalog.get_operation("GET /search")
    assert initial is not None
    configs = [
        InputGeneratorConfig.model_validate(
            {
                **item.model_dump(mode="json"),
                "strategy": strategy,
            }
        )
        for item in initial.configs
    ]

    replaced = _apply_accepted_configs(catalog, initial, configs)

    assert replaced.enabled is True
    assert replaced.configs[0].strategy.type == strategy["type"]


def test_accepted_patch_with_feedback_generator_clears_recoverable_reason(
    tmp_path: Path,
) -> None:
    """An accepted Patch clears the recoverable reason for its repaired input."""
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
    initial = catalog.get_operation("GET /search")
    assert initial is not None
    assert initial.enabled is False
    assert {reason.code for reason in initial.disabled_reasons} == {
        "default_generator_unavailable"
    }
    node_id = initial.configs[0].input_node_id
    assert {reason.input_node_id for reason in initial.disabled_reasons} == {
        node_id
    }

    patched = _apply_accepted_patch(
        catalog,
        initial.operation_key,
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


def test_accepted_patch_clears_recoverable_reasons_only_for_updated_nodes(
    tmp_path: Path,
) -> None:
    """An accepted Patch preserves reasons owned by untouched inputs."""
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
    initial = catalog.get_operation("GET /search")
    assert initial is not None
    node_ids = {
        parameter.name: parameter.input_node_id
        for parameter in initial.snapshot.parameters
    }
    assert {
        reason.input_node_id
        for reason in initial.disabled_reasons
        if reason.recoverable
    } == set(node_ids.values())

    first = _apply_accepted_patch(
        catalog,
        initial.operation_key,
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

    second = _apply_accepted_patch(
        catalog,
        first.operation_key,
        updates=[
            {
                "input_node_id": node_ids["second"],
                "strategy": {"type": "constant", "value": "feedback-second"},
            }
        ],
    )
    assert second.enabled is True
    assert second.disabled_reasons == []


def test_accepted_patch_keeps_unrelated_default_configuration_failure(
    tmp_path: Path,
) -> None:
    """Repairing one input does not hide another default-generation failure."""
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
    initial = catalog.get_operation("POST /search")
    assert initial is not None
    query_id = initial.snapshot.parameters[0].input_node_id
    body_id = initial.snapshot.media_type_node_ids["application/json"]

    assert {
        reason.input_node_id
        for reason in initial.disabled_reasons
        if reason.recoverable
    } == {query_id, body_id}

    patched = _apply_accepted_patch(
        catalog,
        initial.operation_key,
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
    from restscope.db.adapters.request_generation import (
        SqlAlchemyGeneratorConfigRepository,
    )

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


def test_repository_compare_and_swap_rejects_stale_current_content(
    tmp_path: Path,
) -> None:
    """A second writer cannot overwrite content changed after it was read."""
    from restscope.db import SqlAlchemyGeneratorConfigUnitOfWork, make_session_factory
    from restscope.request_generation.ports import GeneratorConfigConcurrentWrite

    catalog, engine = _catalog(tmp_path)
    catalog.initialize_once(_ir())
    created = catalog.get_operation("POST /orders/{orderId}")
    assert created is not None
    session_factory = make_session_factory(engine)

    with (
        SqlAlchemyGeneratorConfigUnitOfWork(session_factory) as first,
        SqlAlchemyGeneratorConfigUnitOfWork(session_factory) as second,
    ):
        from restscope.request_generation import InputGeneratorPatch, prepare_accepted_generator_patch

        stale = first.generator_configs.get_inputs(created.operation_key)
        assert second.generator_configs.get_inputs(created.operation_key) == stale
        target = next(
            item for item in created.configs if item.inclusion_probability < 1
        )
        updated = prepare_accepted_generator_patch(
            created,
            [
                InputGeneratorPatch(
                    input_node_id=target.input_node_id,
                    inclusion_probability=0,
                )
            ],
        )
        first.generator_configs.replace_inputs(
            operation_key=created.operation_key,
            expected=stale,
            updated=updated.configs,
        )
        first.commit()

        with pytest.raises(GeneratorConfigConcurrentWrite):
            second.generator_configs.replace_inputs(
                operation_key=created.operation_key,
                expected=stale,
                updated=updated.configs,
            )


def test_explicit_leaf_presence_patch_closes_all_request_body_ancestors() -> None:
    """A leaf made mandatory by a Patch must not disappear with an optional parent."""
    from restscope.openapi_parser import OpenAPIParser
    from restscope.request_generation import (
        InputGeneratorConfig,
        InputGeneratorPatch,
        build_semantic_input_map,
        expand_generator_patch_presence,
    )
    from restscope.request_generation.snapshot import build_initial_operation_config

    operation = OpenAPIParser.parse(
        {
            "openapi": "3.0.3",
            "info": {"title": "Presence closure", "version": "1"},
            "paths": {
                "/projects": {
                    "post": {
                        "requestBody": {
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "project": {
                                                "type": "object",
                                                "properties": {
                                                    "startDate": {
                                                        "type": "string"
                                                    },
                                                    "endDate": {
                                                        "type": "string"
                                                    },
                                                },
                                            }
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
    ).operations["POST /projects"]
    initial = build_initial_operation_config(operation)
    optional = initial.model_copy(
        update={
            "configs": [
                InputGeneratorConfig.model_validate(
                    {
                        **item.model_dump(mode="json"),
                        "inclusion_probability": 0.5,
                    }
                )
                for item in initial.configs
            ]
        }
    )
    semantic = build_semantic_input_map(optional)
    leaf_id = semantic.node_by_handle["body.project.startDate"]

    expanded = expand_generator_patch_presence(
        optional,
        [
            InputGeneratorPatch(
                input_node_id=leaf_id,
                inclusion_probability=1,
            )
        ],
    )

    by_id = {item.input_node_id: item for item in expanded}
    nodes = {item.input_node_id: item for item in optional.snapshot.input_nodes}
    expected_ids = {leaf_id}
    current_id = leaf_id
    while nodes[current_id].parent_node_id is not None:
        current_id = nodes[current_id].parent_node_id
        expected_ids.add(current_id)
    assert set(by_id) == expected_ids
    assert all(item.inclusion_probability == 1 for item in expanded)


def test_presence_closure_deduplicates_shared_ancestors_and_rejects_conflict() -> None:
    """Shared ancestors are synthesized once; an explicit optional ancestor wins as an error."""
    from tests._operation_smoke_resolution_fixtures import (
        request_body_date_config,
    )

    from restscope.request_generation import (
        GeneratorConfigError,
        InputGeneratorConfig,
        InputGeneratorPatch,
        build_semantic_input_map,
        expand_generator_patch_presence,
    )

    initial = request_body_date_config()
    optional = initial.model_copy(
        update={
            "configs": [
                InputGeneratorConfig.model_validate(
                    {
                        **item.model_dump(mode="json"),
                        "inclusion_probability": 0.5,
                    }
                )
                for item in initial.configs
            ]
        }
    )
    semantic = build_semantic_input_map(optional)
    start_id = semantic.node_by_handle["body.project.startDate"]
    end_id = semantic.node_by_handle["body.project.endDate"]
    project_id = semantic.node_by_handle["body.project"]

    expanded = expand_generator_patch_presence(
        optional,
        [
            InputGeneratorPatch(
                input_node_id=start_id,
                inclusion_probability=1,
            ),
            InputGeneratorPatch(
                input_node_id=end_id,
                inclusion_probability=1,
            ),
        ],
    )

    assert len({item.input_node_id for item in expanded}) == len(expanded)
    assert sum(item.input_node_id == project_id for item in expanded) == 1
    with pytest.raises(GeneratorConfigError) as caught:
        expand_generator_patch_presence(
            optional,
            [
                InputGeneratorPatch(
                    input_node_id=start_id,
                    inclusion_probability=1,
                ),
                InputGeneratorPatch(
                    input_node_id=project_id,
                    inclusion_probability=0.5,
                ),
            ],
        )
    assert caught.value.code == "presence_closure_conflict"
