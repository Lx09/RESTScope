from __future__ import annotations

from dataclasses import fields


def _spec_with_duplicate_operation_id() -> dict:
    return {
        "openapi": "3.0.3",
        "info": {"title": "Pets", "version": "1.0.0"},
        "paths": {
            "/pets": {
                "get": {
                    "operationId": "getPet",
                    "responses": {"200": {"description": "ok"}},
                }
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


def test_parser_builds_only_operation_lookup_indexes() -> None:
    from restscope.openapi_parser import OpenAPIParser
    from restscope.openapi_parser.ir import SpecIndexesIR

    ir = OpenAPIParser().parse(_spec_with_duplicate_operation_id())

    assert ir.indexes.by_operation_id == {"getPet": "GET /pets"}
    assert ir.indexes.by_method_path == {
        ("get", "/pets"): "GET /pets",
        ("get", "/pets/{id}"): "GET /pets/{id}",
    }
    assert [field.name for field in fields(SpecIndexesIR)] == [
        "by_operation_id",
        "by_method_path",
    ]
    for removed_name in (
        "resources",
        "constraint_tags",
        "operation_resource_map",
        "value_index",
        "operation_cards",
        "flow_graph",
    ):
        assert not hasattr(ir.indexes, removed_name)


def test_duplicate_operation_id_warning_is_preserved() -> None:
    from restscope.openapi_parser import OpenAPIParser

    ir = OpenAPIParser().parse(_spec_with_duplicate_operation_id())

    warnings = [
        warning
        for warning in ir.diagnostics.spec_warnings
        if warning.code == "DUPLICATE_OPERATION_ID"
    ]
    assert len(warnings) == 1
    assert "getPet" in warnings[0].message


def test_postprocess_facade_only_exports_schema_sync_utilities() -> None:
    from restscope.openapi_parser import postprocess

    assert postprocess.__all__ == [
        "infer_schema_from_value",
        "merge_schemas",
        "schema_matches",
    ]


def test_removed_postprocessing_ir_types_are_not_available() -> None:
    from restscope.openapi_parser import ir

    for removed_name in (
        "ResourceIndexIR",
        "ConstraintTagIR",
        "ValueRefIR",
        "ValueIndexIR",
        "OperationCardIR",
        "FlowEdgeIR",
        "FlowGraphIR",
    ):
        assert not hasattr(ir, removed_name)
