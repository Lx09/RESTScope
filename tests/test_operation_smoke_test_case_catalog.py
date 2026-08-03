"""Behavior contracts for the Operation Smoke in-memory Test Case Catalog.

The Catalog is the single run-local source of request values and failed HTTP
evidence. These tests use only its public record/query Interface so its internal
storage can change without rewriting the scenarios.
"""

from __future__ import annotations


def test_catalog_assigns_ids_and_answers_typed_bidirectional_queries() -> None:
    """One case supports Parameter and response-field lookup in both directions."""
    from restscope.operation_smoke.test_case_catalog import (
        CatalogTestCaseDraft,
        HTTPFailure,
        TestCaseCatalog,
    )

    catalog = TestCaseCatalog(
        valid_parameters={"body.name", "body.namespace_id", "query.active"}
    )
    recorded = catalog.record(
        CatalogTestCaseDraft(
            parameters={
                "body.name": "demo",
                "body.namespace_id": 900001,
                "query.active": True,
            },
            response_body={
                "message": {
                    "namespace_id": ["Namespace is not valid"],
                    "retry": 900001,
                }
            },
            failure=HTTPFailure(
                status_code=400,
                messages=["HTTP 400: namespace_id: Namespace is not valid"],
            ),
        )
    )

    assert recorded.case_id == "TC1"
    assert catalog.case_range == "TC1"
    assert catalog.get_parameter_value(
        case_ids=["TC1"],
        parameter="body.namespace_id",
    ) == {
        "cases": {
            "TC1": {
                "parameter": "body.namespace_id",
                "status": "parameter_used_in_request",
                "value": 900001,
            }
        },
    }
    assert catalog.find_parameters_by_value(
        case_ids=["TC1"],
        value=900001,
    )["cases"]["TC1"]["parameters"] == ["body.namespace_id"]
    assert catalog.get_response_field_value(
        case_ids=["TC1"],
        field="body.message.retry",
    )["cases"]["TC1"]["value"] == 900001
    assert catalog.find_response_fields_by_value(
        case_ids=["TC1"],
        value=900001,
    )["cases"]["TC1"]["fields"] == ["body.message.retry"]
    assert catalog.get_failure_messages(
        case_ids=["TC1"],
    )["cases"]["TC1"]["messages"] == [
        "HTTP 400: namespace_id: Namespace is not valid"
    ]


def test_catalog_reports_whether_each_request_used_one_parameter() -> None:
    """Parameter lookup states whether the request used the named input.

    A model must not mistake an omitted input for a failed lookup and retry the
    same query.  The absent result therefore uses a complete domain status and
    does not contain a meaningless value placeholder.
    """
    from restscope.operation_smoke.test_case_catalog import (
        CatalogTestCaseDraft,
        TestCaseCatalog,
    )

    catalog = TestCaseCatalog(valid_parameters={"body.name", "query.optional"})
    recorded = catalog.record(
        CatalogTestCaseDraft(
            parameters={"body.name": "created"},
            response_body=None,
            failure=None,
        )
    )

    assert recorded.case_id == "TC1"
    result = catalog.get_parameter_value(
        case_ids=["TC1"],
        parameter="query.optional",
    )

    assert result["cases"]["TC1"] == {
        "parameter": "query.optional",
        "status": "parameter_not_used_in_request",
    }
    assert "value" not in result["cases"]["TC1"]

    used = catalog.get_parameter_value(
        case_ids=["TC1"],
        parameter="body.name",
    )
    assert used["cases"]["TC1"] == {
        "parameter": "body.name",
        "status": "parameter_used_in_request",
        "value": "created",
    }


def test_catalog_dto_rejects_a_body_without_a_4xx_or_5xx_failure() -> None:
    """Success, redirect, and transport cases cannot retain response bodies."""
    import pytest
    from pydantic import ValidationError

    from restscope.operation_smoke.test_case_catalog import CatalogTestCaseDraft

    with pytest.raises(ValidationError, match="only for a 4xx/5xx"):
        CatalogTestCaseDraft(
            parameters={},
            response_body={"large": "success body"},
            failure=None,
        )


def test_catalog_distinguishes_response_body_and_field_absence() -> None:
    """Response lookup explains why a requested value is unavailable."""
    from restscope.operation_smoke.test_case_catalog import (
        CatalogTestCaseDraft,
        HTTPFailure,
        TestCaseCatalog,
    )

    catalog = TestCaseCatalog(valid_parameters=set())
    for body in (None, {"message": "bad"}, {"code": 42}):
        catalog.record(
            CatalogTestCaseDraft(
                parameters={},
                response_body=body,
                failure=HTTPFailure(status_code=400, messages=["HTTP 400"]),
            )
        )

    result = catalog.get_response_field_value(
        case_ids=["TC1", "TC2", "TC3"],
        field="body.code",
    )

    assert result == {
        "cases": {
            "TC1": {
                "field": "body.code",
                "status": "response_body_not_retained",
            },
            "TC2": {
                "field": "body.code",
                "status": "response_field_not_present_in_retained_body",
            },
            "TC3": {
                "field": "body.code",
                "status": "response_field_present_in_retained_body",
                "value": 42,
            },
        }
    }


def test_response_field_path_is_validated_when_body_was_not_retained() -> None:
    """An unretained body must not make a forged field path look valid."""
    import pytest

    from restscope.operation_smoke.test_case_catalog import (
        CatalogTestCaseDraft,
        TestCaseCatalog,
    )

    catalog = TestCaseCatalog(valid_parameters=set())
    catalog.record(CatalogTestCaseDraft(parameters={}))

    with pytest.raises(KeyError, match="must start with body"):
        catalog.get_response_field_value(
            case_ids=["TC1"],
            field="message",
        )


def test_catalog_comparison_is_type_sensitive() -> None:
    """Boolean true must not match integer one during reverse lookup."""
    from restscope.operation_smoke.test_case_catalog import (
        CatalogTestCaseDraft,
        TestCaseCatalog,
    )

    catalog = TestCaseCatalog(valid_parameters={"query.bool", "query.int"})
    catalog.record(
        CatalogTestCaseDraft(
            parameters={"query.bool": True, "query.int": 1},
            response_body=None,
            failure=None,
        )
    )

    result = catalog.find_parameters_by_value(
        case_ids=["TC1"],
        value=True,
    )

    assert result["cases"]["TC1"]["parameters"] == ["query.bool"]


def test_catalog_tool_returns_bounded_native_json_and_rejects_forged_refs() -> None:
    """Five explicit tools return compact JSON and reject forged references."""
    from restscope.capabilities import AgentToolbox
    from restscope.llm import ToolCall
    from restscope.operation_smoke.test_case_catalog import (
        CatalogTestCaseDraft,
        HTTPFailure,
        TestCaseCatalog,
        register_test_case_tools,
        tool_result_json,
    )

    long_value = "x" * 10_000
    catalog = TestCaseCatalog(valid_parameters={"body.name"})
    catalog.record(
        CatalogTestCaseDraft(
            parameters={"body.name": long_value},
            response_body={"message": "invalid"},
            failure=HTTPFailure(
                status_code=400,
                messages=["HTTP 400: invalid"],
            ),
        )
    )
    toolbox = AgentToolbox()
    register_test_case_tools(toolbox=toolbox, catalog=catalog)

    specs = toolbox.specs()
    assert [spec.name for spec in specs] == [
        "test_case.get_parameter_value",
        "test_case.find_parameters_by_value",
        "test_case.get_response_field_value",
        "test_case.find_response_fields_by_value",
        "test_case.get_failure_messages",
    ]
    assert all(
        "action" not in spec.input_schema["properties"]
        for spec in specs
    )
    for spec in specs:
        case_ids_schema = spec.input_schema["properties"]["case_ids"]
        assert case_ids_schema["minItems"] == 1
        assert case_ids_schema["maxItems"] == 20
        assert case_ids_schema["uniqueItems"] is True
    result = toolbox.execute(
        ToolCall(
            id="catalog-1",
            name="test_case.get_parameter_value",
            arguments={
                "case_ids": ["TC1"],
                "parameter": "body.name",
            },
        )
    )

    rendered = tool_result_json(result)
    assert rendered.startswith("{")
    assert "```" not in rendered
    clipped = result.structured["cases"]["TC1"]["value"]
    assert clipped["truncated"] is True
    assert clipped["original_chars"] == 10_000
    assert result.structured["cases"]["TC1"]["status"] == (
        "parameter_used_in_request"
    )
    assert "action" not in result.structured
    assert catalog.get_case("TC1").parameters["body.name"] == long_value

    forged = toolbox.execute(
        ToolCall(
            id="catalog-forged",
            name="test_case.get_failure_messages",
            arguments={
                "case_ids": ["TC99"],
            },
        )
    )
    assert forged.status == "failed"
    assert forged.error["code"] == "invalid_test_case_query"
