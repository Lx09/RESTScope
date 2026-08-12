"""Shared response-field identity across OpenAPI, observations, and tools."""

from __future__ import annotations


def test_response_reference_converts_one_path_between_all_shared_grammars() -> None:
    """One Reference owns semantic, observed, and concrete-array spellings."""
    from restscope.operation_references import ResponseFieldReference

    reference = (
        ResponseFieldReference.body()
        .property("projects")
        .items()
        .property("project_id")
    )

    assert reference.handle == "body.projects[].project_id"
    assert reference.selector == "$.projects[].project_id"
    assert (
        ResponseFieldReference.from_selector("$.projects[].project_id")
        == reference
    )
    assert (
        ResponseFieldReference.from_handle("body.projects[3].project_id")
        == reference
    )


def test_response_reference_keeps_schema_branch_identity_out_of_json_selector() -> None:
    """Combiner branches are unique Schema handles but not runtime JSON keys."""
    from restscope.operation_references import ResponseFieldReference

    reference = (
        ResponseFieldReference.body()
        .variant("oneOf", 1)
        .property("id")
    )

    assert reference.handle == "body.oneOf[1].id"
    assert reference.selector == "$.id"


def test_response_reference_selects_values_from_nested_objects_and_arrays() -> None:
    """One parsed Reference traverses runtime JSON without a second parser."""
    from restscope.operation_references import ResponseFieldReference

    reference = ResponseFieldReference.from_selector("$.projects[].owner.id")

    assert reference.select_values(
        {
            "projects": [
                {"owner": {"id": 7}},
                {"owner": {"id": None}},
                {"owner": {}},
                {"owner": {"id": {"nested": True}}},
            ]
        }
    ) == (7, None, {"nested": True})


def test_response_reference_selects_root_array_values_and_ignores_missing_steps() -> None:
    """Root arrays expand while incompatible or absent branches add no value."""
    from restscope.operation_references import ResponseFieldReference

    reference = ResponseFieldReference.from_selector("$[].code")

    assert reference.select_values(
        [{"code": "a"}, {"other": "b"}, 3, {"code": "c"}]
    ) == ("a", "c")
