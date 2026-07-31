"""Behavior contracts for the Operation Smoke in-memory Test Case Catalog.

The Catalog is the single run-local source of request values and failed HTTP
evidence. These tests use only its public record/query Interface so its internal
storage can change without rewriting the scenarios.
"""

from __future__ import annotations


def test_catalog_assigns_ids_and_answers_typed_bidirectional_queries() -> None:
    """One case supports Parameter and response-field lookup in both directions."""
    from restscope.operation_smoke.test_case_catalog import (
        CatalogQuery,
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
    assert catalog.query(
        CatalogQuery(
            action="parameter_value",
            case_ids=["TC1"],
            name="body.namespace_id",
        )
    ) == {
        "action": "parameter_value",
        "cases": {
            "TC1": {
                "parameter": "body.namespace_id",
                "present": True,
                "value": 900001,
            }
        },
    }
    assert catalog.query(
        CatalogQuery(
            action="parameters_using_value",
            case_ids=["TC1"],
            value=900001,
        )
    )["cases"]["TC1"]["parameters"] == ["body.namespace_id"]
    assert catalog.query(
        CatalogQuery(
            action="response_field_value",
            case_ids=["TC1"],
            name="body.message.retry",
        )
    )["cases"]["TC1"]["value"] == 900001
    assert catalog.query(
        CatalogQuery(
            action="response_fields_using_value",
            case_ids=["TC1"],
            value=900001,
        )
    )["cases"]["TC1"]["fields"] == ["body.message.retry"]
    assert catalog.query(
        CatalogQuery(action="failure_messages", case_ids=["TC1"])
    )["cases"]["TC1"]["messages"] == [
        "HTTP 400: namespace_id: Namespace is not valid"
    ]


def test_catalog_keeps_success_cases_without_body_and_marks_omission() -> None:
    """A 2xx case remains queryable even though its response body is deliberately absent."""
    from restscope.operation_smoke.test_case_catalog import (
        CatalogQuery,
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
    assert catalog.query(
        CatalogQuery(
            action="parameter_value",
            case_ids=["TC1"],
            name="query.optional",
        )
    )["cases"]["TC1"] == {
        "parameter": "query.optional",
        "present": False,
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


def test_catalog_comparison_is_type_sensitive() -> None:
    """Boolean true must not match integer one during reverse lookup."""
    from restscope.operation_smoke.test_case_catalog import (
        CatalogQuery,
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

    result = catalog.query(
        CatalogQuery(
            action="parameters_using_value",
            case_ids=["TC1"],
            value=True,
        )
    )

    assert result["cases"]["TC1"]["parameters"] == ["query.bool"]


def test_catalog_tool_returns_bounded_native_json_and_rejects_forged_refs() -> None:
    """Agent tools receive compact JSON while the Catalog keeps the full value."""
    from restscope.llm import ToolCall
    from restscope.observability import TracingRuntime
    from restscope.operation_smoke.test_case_catalog import (
        CatalogTestCaseDraft,
        HTTPFailure,
        TestCaseCatalog,
        execute_catalog_query,
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
    result = execute_catalog_query(
        catalog=catalog,
        tool_call=ToolCall(
            id="catalog-1",
            name="query_test_case_catalog",
            arguments={
                "action": "parameter_value",
                "case_ids": ["TC1"],
                "name": "body.name",
            },
        ),
        tracing_runtime=TracingRuntime.disabled(),
    )

    rendered = tool_result_json(result)
    assert rendered.startswith("{")
    assert "```" not in rendered
    clipped = result.structured["cases"]["TC1"]["value"]
    assert clipped["truncated"] is True
    assert clipped["original_chars"] == 10_000
    assert catalog.get_case("TC1").parameters["body.name"] == long_value

    forged = execute_catalog_query(
        catalog=catalog,
        tool_call=ToolCall(
            id="catalog-forged",
            name="query_test_case_catalog",
            arguments={
                "action": "failure_messages",
                "case_ids": ["TC99"],
            },
        ),
        tracing_runtime=TracingRuntime.disabled(),
    )
    assert forged.status == "failed"
    assert forged.error["code"] == "invalid_catalog_query"
