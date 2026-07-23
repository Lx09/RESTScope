from __future__ import annotations


def _spec() -> dict:
    return {
        "openapi": "3.0.3",
        "info": {"title": "Input nodes", "version": "1"},
        "paths": {
            "/orders/{orderId}": {
                "post": {
                    "parameters": [
                        {
                            "name": "orderId",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string", "minLength": 1},
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
                                    "required": ["customer"],
                                    "properties": {
                                        "customer": {
                                            "type": "object",
                                            "required": ["id"],
                                            "properties": {"id": {"type": "integer", "minimum": 1}},
                                        },
                                        "items": {
                                            "type": "array",
                                            "minItems": 1,
                                            "items": {
                                                "type": "object",
                                                "properties": {"sku": {"type": "string"}},
                                            },
                                        },
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


def test_parser_builds_stable_operation_input_nodes_with_canonical_parents() -> None:
    from restscope.openapi_parser import OpenAPIParser

    operation = OpenAPIParser.parse(_spec()).operations["POST /orders/{orderId}"]
    by_path = {node.canonical_path: node for node in operation.input_nodes.values()}

    assert set(by_path) == {
        "path/orderId",
        "query/verbose",
        "body",
        "body/application~1json",
        "body/application~1json/properties/customer",
        "body/application~1json/properties/customer/properties/id",
        "body/application~1json/properties/items",
        "body/application~1json/properties/items/items",
        "body/application~1json/properties/items/items/properties/sku",
    }
    assert by_path["path/orderId"].node_kind == "parameter"
    assert by_path["body"].node_kind == "request_body"
    assert by_path["body/application~1json/properties/customer"].node_kind == "object"
    assert by_path["body/application~1json/properties/items"].node_kind == "array"
    assert by_path["body/application~1json/properties/customer/properties/id"].node_kind == "scalar"

    customer = by_path["body/application~1json/properties/customer"]
    customer_id = by_path["body/application~1json/properties/customer/properties/id"]
    assert customer_id.parent_node_id == customer.input_node_id
    assert customer.schema is operation.request_body.contents["application/json"].schema.properties["customer"]

    reparsed = OpenAPIParser.parse(_spec()).operations[operation.operation_key]
    assert {
        path: node.input_node_id
        for path, node in by_path.items()
    } == {
        node.canonical_path: node.input_node_id
        for node in reparsed.input_nodes.values()
    }
    assert all(not hasattr(node, "fingerprint") for node in by_path.values())


def test_input_nodes_expand_complex_parameters_and_schema_variants() -> None:
    from restscope.openapi_parser import OpenAPIParser

    spec = {
        "openapi": "3.0.3",
        "info": {"title": "Nested inputs", "version": "1"},
        "paths": {
            "/search": {
                "post": {
                    "parameters": [
                        {
                            "name": "filter",
                            "in": "query",
                            "style": "deepObject",
                            "explode": True,
                            "schema": {
                                "type": "object",
                                "properties": {"status": {"type": "string"}},
                            },
                        }
                    ],
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "subject": {
                                            "oneOf": [
                                                {
                                                    "type": "object",
                                                    "properties": {"userId": {"type": "integer"}},
                                                },
                                                {
                                                    "type": "object",
                                                    "properties": {"teamId": {"type": "integer"}},
                                                },
                                            ]
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

    operation = OpenAPIParser.parse(spec).operations["POST /search"]
    by_path = {node.canonical_path: node for node in operation.input_nodes.values()}

    assert "query/filter/properties/status" in by_path
    assert "body/application~1json/properties/subject/oneOf/0" in by_path
    assert "body/application~1json/properties/subject/oneOf/0/properties/userId" in by_path
    assert "body/application~1json/properties/subject/oneOf/1" in by_path
    assert "body/application~1json/properties/subject/oneOf/1/properties/teamId" in by_path
    assert by_path["body/application~1json/properties/subject"].node_kind == "variant"


def test_input_node_identity_ignores_parameter_order_ref_location_and_descriptions() -> None:
    from copy import deepcopy

    from restscope.openapi_parser import OpenAPIParser

    inline = _spec()
    inline["paths"]["/orders/{orderId}"]["post"]["parameters"].reverse()
    inline["paths"]["/orders/{orderId}"]["post"]["description"] = "new operation text"
    inline_body = inline["paths"]["/orders/{orderId}"]["post"]["requestBody"]["content"][
        "application/json"
    ]["schema"]

    referenced = deepcopy(inline)
    referenced["components"] = {"schemas": {"MovedBody": deepcopy(inline_body)}}
    referenced["paths"]["/orders/{orderId}"]["post"]["requestBody"]["content"][
        "application/json"
    ]["schema"] = {"$ref": "#/components/schemas/MovedBody"}
    referenced["components"]["schemas"]["MovedBody"]["description"] = "display only"

    first = OpenAPIParser.parse(inline).operations["POST /orders/{orderId}"]
    second = OpenAPIParser.parse(referenced).operations[first.operation_key]

    assert {
        node.canonical_path: node.input_node_id
        for node in first.input_nodes.values()
    } == {
        node.canonical_path: node.input_node_id
        for node in second.input_nodes.values()
    }


def test_new_optional_sibling_preserves_existing_node_ids() -> None:
    from copy import deepcopy

    from restscope.openapi_parser import OpenAPIParser

    original_spec = _spec()
    changed_spec = deepcopy(original_spec)
    properties = changed_spec["paths"]["/orders/{orderId}"]["post"]["requestBody"][
        "content"
    ]["application/json"]["schema"]["properties"]
    properties["note"] = {"type": "string", "description": "new optional sibling"}

    original = OpenAPIParser.parse(original_spec).operations["POST /orders/{orderId}"]
    changed = OpenAPIParser.parse(changed_spec).operations[original.operation_key]
    original_by_path = {node.canonical_path: node for node in original.input_nodes.values()}
    changed_by_path = {node.canonical_path: node for node in changed.input_nodes.values()}

    assert "body/application~1json/properties/note" in changed_by_path
    for path, node in original_by_path.items():
        assert changed_by_path[path].input_node_id == node.input_node_id


def test_header_and_media_type_casing_do_not_change_semantic_input_identity() -> None:
    from copy import deepcopy

    from restscope.openapi_parser import OpenAPIParser

    first_spec = {
        "openapi": "3.0.3",
        "info": {"title": "Casing", "version": "1"},
        "paths": {
            "/items": {
                "post": {
                    "parameters": [
                        {"name": "X-Trace", "in": "header", "schema": {"type": "string"}}
                    ],
                    "requestBody": {
                        "content": {
                            "Application/JSON": {"schema": {"type": "string"}}
                        }
                    },
                    "responses": {"200": {"description": "ok"}},
                }
            }
        },
    }
    second_spec = deepcopy(first_spec)
    operation = second_spec["paths"]["/items"]["post"]
    operation["parameters"][0]["name"] = "x-trace"
    media = operation["requestBody"]["content"].pop("Application/JSON")
    operation["requestBody"]["content"]["application/json"] = media

    first = OpenAPIParser.parse(first_spec).operations["POST /items"]
    second = OpenAPIParser.parse(second_spec).operations["POST /items"]

    assert {
        node.canonical_path: node.input_node_id
        for node in first.input_nodes.values()
    } == {
        node.canonical_path: node.input_node_id
        for node in second.input_nodes.values()
    }
    assert {node.canonical_path for node in first.input_nodes.values()} == {
        "header/x-trace",
        "body",
        "body/application~1json",
    }
