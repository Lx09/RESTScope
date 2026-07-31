"""Behavioral contracts for Smoke references backed by monitor evidence."""

from __future__ import annotations

from types import SimpleNamespace

from tests._operation_smoke_plan_solve_fixtures import smoke_config


class StubReferenceCoordinator:
    """Expose one populated project identifier pool and no response pools."""

    def __init__(self) -> None:
        """Build the narrow catalog shape consumed by the public adapter."""
        catalog = SimpleNamespace(
            list_resources=lambda **_kwargs: [
                SimpleNamespace(canonical_name="project")
            ]
        )
        self.resource_identifier_tracker = SimpleNamespace(catalog=catalog)

    def available_response_value_sources(self, **_kwargs):
        """Keep this scenario focused on resource identifiers."""
        return []

    def lookup(self, _request):
        """Return one integer project identifier with its plural alias."""
        return SimpleNamespace(
            canonical_resource="project",
            aliases=["projects"],
            identifiers=[SimpleNamespace(value=21, value_type="integer")],
            operations=[],
        )


def test_resource_identifier_pool_is_offered_only_to_semantic_id_inputs() -> None:
    """Project IDs must not become choices for unrelated scalar Parameters."""
    from restscope.operation_smoke import BehaviorMonitorReferenceValues
    from restscope.testing import ParameterSnapshot

    config = smoke_config()
    config = config.model_copy(
        update={
            "snapshot": config.snapshot.model_copy(
                update={
                    "parameters": [
                        ParameterSnapshot(
                            input_node_id="path/projectId",
                            name="projectId",
                            location="path",
                            required=True,
                        ),
                        ParameterSnapshot(
                            input_node_id="query/region",
                            name="region",
                            location="query",
                            required=False,
                        ),
                    ]
                }
            )
        }
    )

    options = BehaviorMonitorReferenceValues(
        StubReferenceCoordinator()
    ).available_options(
        ir=SimpleNamespace(),
        config=config,
    )

    assert [
        (option.input_node_id, option.canonical_resource)
        for option in options
        if option.kind == "resource_identifier"
    ] == [("path/projectId", "project")]


def test_generic_variant_id_inherits_resource_meaning_from_operation_path() -> None:
    """The integer child of ``/projects/{id}`` may use project identifiers."""
    from restscope.openapi_parser import OpenAPIParser
    from restscope.operation_smoke import BehaviorMonitorReferenceValues
    from restscope.testing.snapshot import build_initial_operation_config

    operation = OpenAPIParser.parse(
        {
            "openapi": "3.0.3",
            "info": {"title": "GitLab ID shape", "version": "1"},
            "paths": {
                "/projects/{id}": {
                    "delete": {
                        "parameters": [
                            {
                                "name": "id",
                                "in": "path",
                                "required": True,
                                "schema": {
                                    "oneOf": [
                                        {"type": "string"},
                                        {"type": "integer"},
                                    ]
                                },
                            }
                        ],
                        "responses": {"202": {"description": "accepted"}},
                    }
                }
            },
        }
    ).operations["DELETE /projects/{id}"]

    config = build_initial_operation_config(operation)
    options = BehaviorMonitorReferenceValues(
        StubReferenceCoordinator()
    ).available_options(
        ir=SimpleNamespace(),
        config=config,
    )
    nodes = {
        node.input_node_id: node
        for node in config.snapshot.input_nodes
    }

    assert [
        nodes[option.input_node_id].canonical_path
        for option in options
        if option.kind == "resource_identifier"
    ] == ["path/id/oneOf/1"]
