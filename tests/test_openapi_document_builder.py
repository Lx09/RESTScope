from __future__ import annotations

from copy import deepcopy

import pytest


def _oas3_spec() -> dict:
    return {
        "openapi": "3.0.3",
        "info": {"title": "Pets", "version": "1.2.3"},
        "servers": [{"url": "https://api.example.com"}],
        "components": {
            "schemas": {
                "Pet": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "nickname": {"type": "string", "nullable": True},
                    },
                    "required": ["id"],
                },
                "Unused": {"type": "object"},
            },
            "securitySchemes": {
                "ApiKey": {"type": "apiKey", "name": "X-API-Key", "in": "header"},
                "OAuth": {
                    "type": "oauth2",
                    "flows": {
                        "clientCredentials": {
                            "tokenUrl": "https://auth.example.com/token",
                            "scopes": {"read": "Read pets"},
                        }
                    },
                },
                "UnusedAuth": {"type": "http", "scheme": "bearer"},
            },
        },
        "paths": {
            "/pets": {
                "summary": "Pet collection",
                "x-path-scope": "public",
                "get": {
                    "operationId": "listPets",
                    "summary": "Original summary",
                    "x-operation-scope": "read",
                    "security": [
                        {"ApiKey": [], "OAuth": ["read"]},
                        {"OAuth": ["read"]},
                    ],
                    "responses": {
                        "200": {
                            "description": "ok",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "array",
                                        "items": {"$ref": "#/components/schemas/Pet"},
                                    }
                                }
                            },
                            "links": {
                                "getPet": {"operationId": "getPet"},
                            },
                        }
                    },
                },
                "post": {
                    "operationId": "createPet",
                    "servers": [{"url": "https://write.example.com"}],
                    "callbacks": {"created": {"$ref": "#/components/callbacks/Created"}},
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/Pet"}
                            }
                        },
                    },
                    "responses": {"201": {"description": "created"}},
                },
            },
            "/pets/{id}": {
                "get": {
                    "operationId": "getPet",
                    "parameters": [
                        {
                            "name": "id",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string"},
                        }
                    ],
                    "responses": {"200": {"description": "ok"}},
                }
            },
        },
    }


def _recursive_spec() -> dict:
    return {
        "openapi": "3.1.0",
        "info": {"title": "Nodes", "version": "1"},
        "components": {
            "schemas": {
                "Node": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "child": {"$ref": "#/components/schemas/Node"},
                    },
                },
                "Unused": {"type": "string"},
            }
        },
        "paths": {
            "/nodes": {
                "get": {
                    "operationId": "listNodes",
                    "responses": {
                        "200": {
                            "description": "ok",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/Node"}
                                }
                            },
                        }
                    },
                }
            }
        },
    }


def _swagger2_spec() -> dict:
    return {
        "swagger": "2.0",
        "info": {"title": "Legacy Pets", "version": "1"},
        "host": "legacy.example.com",
        "basePath": "/v1",
        "schemes": ["https"],
        "securityDefinitions": {
            "Basic": {"type": "basic"},
            "Key": {"type": "apiKey", "name": "X-Key", "in": "header"},
            "OAuth": {
                "type": "oauth2",
                "flow": "application",
                "tokenUrl": "https://legacy.example.com/token",
                "scopes": {"write": "Write pets"},
            },
        },
        "security": [{"Basic": [], "Key": [], "OAuth": ["write"]}],
        "definitions": {
            "Pet": {
                "type": "object",
                "properties": {"name": {"type": "string"}},
            }
        },
        "paths": {
            "/pets": {
                "get": {
                    "operationId": "listPets",
                    "parameters": [
                        {
                            "name": "limit",
                            "in": "query",
                            "type": "integer",
                            "multipleOf": 2,
                            "minimum": 1,
                        }
                    ],
                    "responses": {
                        "200": {
                            "description": "ok",
                            "headers": {"X-Total": {"type": "integer"}},
                            "schema": {
                                "type": "array",
                                "items": {"$ref": "#/definitions/Pet"},
                            },
                        }
                    },
                },
                "post": {
                    "operationId": "createPet",
                    "parameters": [
                        {
                            "name": "pet",
                            "in": "body",
                            "required": True,
                            "schema": {"$ref": "#/definitions/Pet"},
                        }
                    ],
                    "responses": {"201": {"description": "created"}},
                },
            },
            "/upload": {
                "post": {
                    "operationId": "uploadPet",
                    "consumes": ["multipart/form-data"],
                    "parameters": [
                        {"name": "file", "in": "formData", "required": True, "type": "file"}
                    ],
                    "responses": {"204": {"description": "uploaded"}},
                }
            },
        },
    }


def _recursive_swagger2_spec() -> dict:
    return {
        "swagger": "2.0",
        "info": {"title": "Legacy Nodes", "version": "1"},
        "definitions": {
            "Node": {
                "type": "object",
                "properties": {
                    "child": {"$ref": "#/definitions/Node"},
                },
            }
        },
        "paths": {
            "/nodes": {
                "get": {
                    "operationId": "listNodes",
                    "responses": {
                        "200": {
                            "description": "ok",
                            "schema": {"$ref": "#/definitions/Node"},
                        }
                    },
                }
            }
        },
    }


def _raw_attributes_spec() -> dict:
    return {
        "openapi": "3.1.0",
        "info": {"title": "Raw attributes", "version": "1"},
        "components": {
            "schemas": {
                "Amount": {
                    "type": "number",
                    "multipleOf": 0.01,
                    "x-unit": "USD",
                },
                "RawNode": {
                    "type": "object",
                    "unevaluatedProperties": {
                        "$ref": "#/components/schemas/RawNode"
                    },
                },
            },
            "examples": {
                "Shared": {
                    "summary": "Shared example",
                    "value": {"amount": 1.25},
                    "x-origin": "component",
                    "unexpected": "filtered",
                }
            },
            "securitySchemes": {
                "ApiKey": {
                    "type": "apiKey",
                    "name": "X-Key",
                    "in": "header",
                    "description": "raw description",
                    "flow": "implicit",
                    "tokenUrl": "https://legacy.example.com/token",
                    "scopes": {"read": "Read"},
                    "x-security": {"owner": "tests"},
                    "unexpected": "filtered",
                }
            },
        },
        "paths": {
            "/raw": {
                "post": {
                    "operationId": "exerciseRaw",
                    "security": [{"ApiKey": []}],
                    "parameters": [
                        {
                            "name": "step",
                            "in": "query",
                            "description": "raw description",
                            "deprecated": True,
                            "allowReserved": True,
                            "collectionFormat": "csv",
                            "x-parameter": {"nested": [1, 2]},
                            "unexpected": "filtered",
                            "schema": {
                                "type": "number",
                                "minimum": 1,
                                "maximum": 9,
                                "multipleOf": 0.5,
                                "x-schema": {"nested": [3, 4]},
                            },
                        }
                    ],
                    "requestBody": {
                        "description": "raw body",
                        "required": True,
                        "source": "body_parameter",
                        "form_params": [{"name": "legacy"}],
                        "x-request-body": True,
                        "unexpected": "filtered",
                        "content": {
                            "application/json": {
                                "x-media": "request",
                                "unexpected": "filtered",
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "amount": {
                                            "type": "number",
                                            "multipleOf": 0.01,
                                        }
                                    },
                                },
                                "examples": {
                                    "shared": {
                                        "$ref": "#/components/examples/Shared"
                                    },
                                    "inline": {
                                        "summary": "Inline",
                                        "value": {"amount": 2.5},
                                        "x-example": True,
                                        "unexpected": "filtered",
                                    },
                                },
                            }
                        },
                    },
                    "responses": {
                        "200": {
                            "description": "raw response",
                            "schema": {"type": "string"},
                            "examples": {"application/json": {"legacy": True}},
                            "links": {
                                "next": {"operationId": "exerciseRaw"}
                            },
                            "x-response": True,
                            "unexpected": "filtered",
                            "headers": {
                                "X-Rate": {
                                    "description": "Rate",
                                    "allowReserved": True,
                                    "example": 4,
                                    "collectionFormat": "csv",
                                    "x-header": True,
                                    "unexpected": "filtered",
                                    "schema": {
                                        "type": "number",
                                        "multipleOf": 0.25,
                                    },
                                },
                                "X-Sample": {
                                    "examples": {
                                        "shared": {
                                            "$ref": "#/components/examples/Shared"
                                        }
                                    },
                                    "schema": {"type": "string"},
                                },
                            },
                            "content": {
                                "application/json": {
                                    "x-media": "response",
                                    "unexpected": "filtered",
                                    "schema": {
                                        "type": "object",
                                        "multipleOf": 2,
                                        "minContains": 1,
                                        "properties": {
                                            "amount": {
                                                "$ref": "#/components/schemas/Amount"
                                            },
                                            "values": {
                                                "type": "array",
                                                "contains": {
                                                    "$ref": "#/components/schemas/Amount"
                                                },
                                                "prefixItems": [
                                                    {
                                                        "type": "integer",
                                                        "multipleOf": 2,
                                                    }
                                                ],
                                                "unevaluatedItems": False,
                                            },
                                        },
                                        "if": {
                                            "properties": {
                                                "kind": {"const": "credit"}
                                            }
                                        },
                                        "then": {"required": ["amount"]},
                                        "else": {"maxProperties": 5},
                                        "patternProperties": {
                                            "^x-": {
                                                "type": "string",
                                                "minLength": 2,
                                            }
                                        },
                                        "dependentSchemas": {
                                            "amount": {
                                                "properties": {
                                                    "currency": {
                                                        "type": "string"
                                                    }
                                                }
                                            }
                                        },
                                        "unevaluatedProperties": False,
                                    },
                                }
                            },
                        },
                        "201": {
                            "description": "recursive raw schema",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "array",
                                        "contains": {
                                            "$ref": "#/components/schemas/RawNode"
                                        },
                                    }
                                }
                            },
                        },
                    },
                }
            }
        },
    }


def test_builds_multiple_operations_from_typed_ir() -> None:
    from restscope.openapi_parser import OpenAPIParser, build_openapi_document

    ir = OpenAPIParser().parse(_oas3_spec())
    ir.operations["GET /pets"].summary = "Changed in IR"

    document = build_openapi_document(
        ir,
        ["GET /pets", "POST /pets", "GET /pets"],
    )

    assert document["openapi"] == "3.1.0"
    assert set(document["paths"]) == {"/pets"}
    assert set(document["paths"]["/pets"]) == {
        "summary",
        "x-path-scope",
        "get",
        "post",
    }
    get_operation = document["paths"]["/pets"]["get"]
    post_operation = document["paths"]["/pets"]["post"]
    assert get_operation["summary"] == "Changed in IR"
    assert get_operation["x-operation-scope"] == "read"
    assert get_operation["servers"] == [{"url": "https://api.example.com"}]
    assert post_operation["servers"] == [{"url": "https://write.example.com"}]
    assert "callbacks" not in post_operation
    assert "links" not in get_operation["responses"]["200"]

    pet_schema = get_operation["responses"]["200"]["content"]["application/json"]["schema"]["items"]
    assert pet_schema["properties"]["nickname"]["type"] == ["string", "null"]
    assert "schemas" not in document["components"]
    assert set(document["components"]["securitySchemes"]) == {"ApiKey", "OAuth"}
    assert get_operation["security"] == [
        {"ApiKey": [], "OAuth": ["read"]},
        {"OAuth": ["read"]},
    ]

    document["paths"]["/pets"]["get"]["summary"] = "Changed output"
    assert ir.operations["GET /pets"].summary == "Changed in IR"
    assert OpenAPIParser().parse(document).operations.keys() == {
        "GET /pets",
        "POST /pets",
    }


def test_groups_selected_operations_across_paths_and_validates_keys() -> None:
    from restscope.openapi_parser import (
        OpenAPIParser,
        OperationDocumentGenerationError,
        build_openapi_document,
    )

    ir = OpenAPIParser().parse(_oas3_spec())
    document = build_openapi_document(ir, ["POST /pets", "GET /pets/{id}"])

    assert list(document["paths"]) == ["/pets", "/pets/{id}"]
    assert "post" in document["paths"]["/pets"]
    assert "get" in document["paths"]["/pets/{id}"]

    with pytest.raises(OperationDocumentGenerationError, match="At least one"):
        build_openapi_document(ir, [])
    with pytest.raises(OperationDocumentGenerationError, match="not a string"):
        build_openapi_document(ir, "GET /pets")
    with pytest.raises(OperationDocumentGenerationError, match="MISSING"):
        build_openapi_document(ir, ["GET /pets", "MISSING"])

    ir.operations["GET /pets"].responses.by_status.clear()
    with pytest.raises(OperationDocumentGenerationError, match="no responses"):
        build_openapi_document(ir, ["GET /pets"])


def test_recursive_schemas_use_only_the_minimum_component_closure() -> None:
    from restscope.openapi_parser import (
        OpenAPIParser,
        OperationDocumentGenerationError,
        build_openapi_document,
    )

    ir = OpenAPIParser().parse(_recursive_spec())
    document = build_openapi_document(ir, ["GET /nodes"])

    assert set(document["components"]["schemas"]) == {"Node"}
    response_schema = document["paths"]["/nodes"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
    assert response_schema["properties"]["child"] == {
        "$ref": "#/components/schemas/Node"
    }
    assert document["components"]["schemas"]["Node"]["properties"]["child"] == {
        "$ref": "#/components/schemas/Node"
    }
    assert OpenAPIParser().parse(document).operations

    del ir.components.schemas["Node"]
    with pytest.raises(OperationDocumentGenerationError, match="unavailable"):
        build_openapi_document(ir, ["GET /nodes"])


def test_swagger2_ir_is_normalized_to_openapi31() -> None:
    from restscope.openapi_parser import OpenAPIParser, build_openapi_document

    ir = OpenAPIParser().parse(_swagger2_spec())
    document = build_openapi_document(
        ir,
        ["GET /pets", "POST /pets", "POST /upload"],
    )

    assert document["openapi"] == "3.1.0"
    get_operation = document["paths"]["/pets"]["get"]
    assert get_operation["parameters"][0]["schema"] == {
        "type": "integer",
        "multipleOf": 2,
        "minimum": 1,
    }
    assert get_operation["responses"]["200"]["headers"]["X-Total"]["schema"] == {
        "type": "integer"
    }
    assert get_operation["servers"] == [{"url": "https://legacy.example.com/v1", "description": "Derived from Swagger 2.0 host/basePath/schemes"}]
    assert document["components"]["securitySchemes"]["Basic"] == {
        "type": "http",
        "scheme": "basic",
    }
    assert document["components"]["securitySchemes"]["Key"] == {
        "type": "apiKey",
        "name": "X-Key",
        "in": "header",
    }
    assert document["components"]["securitySchemes"]["OAuth"] == {
        "type": "oauth2",
        "flows": {
            "clientCredentials": {
                "tokenUrl": "https://legacy.example.com/token",
                "scopes": {"write": "Write pets"},
            }
        },
    }
    upload_schema = document["paths"]["/upload"]["post"]["requestBody"]["content"]["multipart/form-data"]["schema"]
    assert upload_schema["properties"]["file"] == {
        "type": "string",
        "format": "binary",
    }
    assert OpenAPIParser().parse(document).operations.keys() == {
        "GET /pets",
        "POST /pets",
        "POST /upload",
    }

    recursive_ir = OpenAPIParser().parse(_recursive_swagger2_spec())
    recursive_document = build_openapi_document(recursive_ir, ["GET /nodes"])
    assert set(recursive_document["components"]["schemas"]) == {"Node"}
    assert recursive_document["components"]["schemas"]["Node"]["properties"]["child"] == {
        "$ref": "#/components/schemas/Node"
    }


def test_raw_schema_attributes_are_normalized_recursively() -> None:
    from restscope.openapi_parser import OpenAPIParser, build_openapi_document

    ir = OpenAPIParser().parse(_raw_attributes_spec())
    raw_snapshot = deepcopy(
        ir.operations["POST /raw"]
        .responses.by_status["200"]
        .contents["application/json"]
        .schema.raw
    )

    document = build_openapi_document(ir, ["POST /raw"])
    operation = document["paths"]["/raw"]["post"]

    assert operation["parameters"][0]["schema"]["multipleOf"] == 0.5
    request_schema = operation["requestBody"]["content"]["application/json"]["schema"]
    assert request_schema["properties"]["amount"]["multipleOf"] == 0.01

    response_schema = operation["responses"]["200"]["content"]["application/json"]["schema"]
    assert response_schema["multipleOf"] == 2
    assert response_schema["minContains"] == 1
    assert response_schema["properties"]["amount"] == {
        "type": "number",
        "multipleOf": 0.01,
        "x-unit": "USD",
    }
    values = response_schema["properties"]["values"]
    assert values["contains"] == {
        "type": "number",
        "multipleOf": 0.01,
        "x-unit": "USD",
    }
    assert values["prefixItems"] == [{"type": "integer", "multipleOf": 2}]
    assert values["unevaluatedItems"] is False
    assert response_schema["if"] == {
        "properties": {"kind": {"const": "credit"}}
    }
    assert response_schema["then"] == {"required": ["amount"]}
    assert response_schema["else"] == {"maxProperties": 5}
    assert response_schema["patternProperties"]["^x-"] == {
        "type": "string",
        "minLength": 2,
    }
    assert response_schema["dependentSchemas"]["amount"] == {
        "properties": {"currency": {"type": "string"}}
    }
    assert response_schema["unevaluatedProperties"] is False

    recursive_schema = operation["responses"]["201"]["content"]["application/json"]["schema"]
    assert recursive_schema["contains"]["unevaluatedProperties"] == {
        "$ref": "#/components/schemas/RawNode"
    }
    assert set(document["components"]["schemas"]) == {"RawNode"}
    assert document["components"]["schemas"]["RawNode"]["unevaluatedProperties"] == {
        "$ref": "#/components/schemas/RawNode"
    }

    response_schema["patternProperties"]["^x-"]["minLength"] = 99
    assert (
        ir.operations["POST /raw"]
        .responses.by_status["200"]
        .contents["application/json"]
        .schema.raw
        == raw_snapshot
    )
    assert OpenAPIParser().parse(document).operations.keys() == {"POST /raw"}


def test_typed_ir_fields_override_or_remove_raw_values() -> None:
    from restscope.openapi_parser import OpenAPIParser, build_openapi_document

    ir = OpenAPIParser().parse(_raw_attributes_spec())
    operation_ir = ir.operations["POST /raw"]
    parameter = operation_ir.query_parameters[0]
    parameter.description = None
    parameter.deprecated = False
    parameter.allow_reserved = False
    parameter.schema.type = None
    parameter.schema.minimum = 3
    parameter.schema.maximum = None
    operation_ir.request_body.description = None
    operation_ir.request_body.required = False
    operation_ir.responses.by_status["200"].headers.clear()
    response_media = operation_ir.responses.by_status["200"].contents["application/json"]
    response_media.schema.properties.clear()

    document = build_openapi_document(ir, ["POST /raw"])
    operation = document["paths"]["/raw"]["post"]
    serialized_parameter = operation["parameters"][0]
    assert "description" not in serialized_parameter
    assert "deprecated" not in serialized_parameter
    assert "allowReserved" not in serialized_parameter
    assert serialized_parameter["schema"] == {
        "minimum": 3,
        "multipleOf": 0.5,
        "x-schema": {"nested": [3, 4]},
    }
    assert "description" not in operation["requestBody"]
    assert "required" not in operation["requestBody"]
    assert "headers" not in operation["responses"]["200"]
    assert "properties" not in operation["responses"]["200"]["content"]["application/json"]["schema"]


def test_non_schema_raw_attributes_are_filtered_and_examples_are_inlined() -> None:
    from restscope.openapi_parser import OpenAPIParser, build_openapi_document

    ir = OpenAPIParser().parse(_raw_attributes_spec())
    parameter_raw = deepcopy(ir.operations["POST /raw"].query_parameters[0].raw)
    example_raw = deepcopy(ir.components.examples["Shared"].raw)
    security_raw = deepcopy(ir.components.security_schemes["ApiKey"].raw)
    document = build_openapi_document(ir, ["POST /raw"])
    operation = document["paths"]["/raw"]["post"]

    parameter = operation["parameters"][0]
    assert parameter["x-parameter"] == {"nested": [1, 2]}
    assert "collectionFormat" not in parameter
    assert "unexpected" not in parameter

    request_body = operation["requestBody"]
    assert request_body["x-request-body"] is True
    assert "source" not in request_body
    assert "form_params" not in request_body
    assert "unexpected" not in request_body
    request_media = request_body["content"]["application/json"]
    assert request_media["x-media"] == "request"
    assert "unexpected" not in request_media
    assert request_media["examples"]["shared"] == {
        "summary": "Shared example",
        "value": {"amount": 1.25},
        "x-origin": "component",
    }
    assert request_media["examples"]["inline"] == {
        "summary": "Inline",
        "value": {"amount": 2.5},
        "x-example": True,
    }

    response = operation["responses"]["200"]
    assert response["x-response"] is True
    assert "schema" not in response
    assert "examples" not in response
    assert "links" not in response
    assert "unexpected" not in response
    assert response["content"]["application/json"]["x-media"] == "response"
    assert "unexpected" not in response["content"]["application/json"]

    rate_header = response["headers"]["X-Rate"]
    assert rate_header["allowReserved"] is True
    assert rate_header["example"] == 4
    assert rate_header["x-header"] is True
    assert "collectionFormat" not in rate_header
    assert "unexpected" not in rate_header
    assert rate_header["schema"]["multipleOf"] == 0.25
    assert response["headers"]["X-Sample"]["examples"]["shared"] == {
        "summary": "Shared example",
        "value": {"amount": 1.25},
        "x-origin": "component",
    }

    security = document["components"]["securitySchemes"]["ApiKey"]
    assert security["x-security"] == {"owner": "tests"}
    assert "flow" not in security
    assert "tokenUrl" not in security
    assert "scopes" not in security
    assert "unexpected" not in security

    parameter["x-parameter"]["nested"].append(3)
    request_media["examples"]["shared"]["value"]["amount"] = 99
    security["x-security"]["owner"] = "changed"
    assert ir.operations["POST /raw"].query_parameters[0].raw == parameter_raw
    assert ir.components.examples["Shared"].raw == example_raw
    assert ir.components.security_schemes["ApiKey"].raw == security_raw


@pytest.mark.parametrize(
    "ref_path",
    [
        "https://example.com/schemas.json#/Amount",
        "#/components/responses/Amount",
        "#/components/schemas/Missing",
    ],
)
def test_raw_schema_reference_failures_are_explicit(ref_path: str) -> None:
    from restscope.openapi_parser import (
        OpenAPIParser,
        OperationDocumentGenerationError,
        build_openapi_document,
    )

    ir = OpenAPIParser().parse(_raw_attributes_spec())
    schema = (
        ir.operations["POST /raw"]
        .responses.by_status["201"]
        .contents["application/json"]
        .schema
    )
    schema.raw["contains"] = {"$ref": ref_path}

    with pytest.raises(OperationDocumentGenerationError, match="reference|component"):
        build_openapi_document(ir, ["POST /raw"])


@pytest.mark.parametrize(
    "ref_path",
    ["https://example.com/example.json", "#/components/examples/Missing"],
)
def test_raw_example_reference_failures_are_explicit(ref_path: str) -> None:
    from restscope.openapi_parser import (
        OpenAPIParser,
        OperationDocumentGenerationError,
        build_openapi_document,
    )

    ir = OpenAPIParser().parse(_raw_attributes_spec())
    example = (
        ir.operations["POST /raw"]
        .request_body.contents["application/json"]
        .examples["shared"]
    )
    example.raw["$ref"] = ref_path

    with pytest.raises(OperationDocumentGenerationError, match="reference|component"):
        build_openapi_document(ir, ["POST /raw"])


def test_public_facades_export_document_builder() -> None:
    import restscope
    from restscope.openapi_parser import build_openapi_document

    assert restscope.build_openapi_document is build_openapi_document
