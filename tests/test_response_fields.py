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
