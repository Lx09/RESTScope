"""Behavioral contracts for Smoke references backed by monitor evidence."""

from __future__ import annotations

from types import SimpleNamespace

def test_response_source_preview_is_read_only_until_apply_registers_it() -> None:
    """Candidate sampling reads history; Apply performs the first pool write."""
    from restscope.openapi_parser import OpenAPIParser
    from restscope.operation_smoke import BehaviorMonitorReferenceValues
    from restscope.harness.testing import InputGeneratorPatch, ResponseValueGenerator
    from restscope.harness.testing.snapshot import build_initial_operation_config

    ir = OpenAPIParser.parse(
        {
            "openapi": "3.0.3",
            "info": {"title": "Response reference", "version": "1"},
            "paths": {
                "/projects/{projectId}": {
                    "get": {
                        "parameters": [
                            {
                                "name": "projectId",
                                "in": "path",
                                "required": True,
                                "schema": {"type": "string"},
                            }
                        ],
                        "responses": {"204": {"description": "ok"}},
                    }
                },
                "/projects": {
                    "get": {
                        "responses": {
                            "200": {
                                "description": "ok",
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "type": "array",
                                            "items": {
                                                "type": "object",
                                                "properties": {
                                                    "id": {"type": "string"}
                                                },
                                            },
                                        }
                                    }
                                },
                            }
                        }
                    }
                },
            },
        }
    )
    config = build_initial_operation_config(
        ir.operations["GET /projects/{projectId}"]
    )

    class HistoricalCatalog:
        """Return retained scalars without exposing a registration method."""

        def historical_values_for_source(self, source, *, limit):
            """Read the exact selected field's two candidate values."""
            assert source.selector == "$[].id"
            assert limit == 100
            return ["project-a", "project-b"]

    class ResponseCoordinator:
        """Separate read-only preview calls from the one Apply registration."""

        def __init__(self):
            self.response_value_tracker = SimpleNamespace(
                catalog=HistoricalCatalog()
            )
            self.register_calls = []

        def preview_selected_response_value_source(self, **arguments):
            """Derive the private name without writing a monitor."""
            assert arguments["source"].field_name == "id"
            return SimpleNamespace(value_name="response_private_digest")

        def register_response_value_sources(self, **arguments):
            """Record the one Apply-time producer-to-consumer registration."""
            self.register_calls.append(arguments)
            return SimpleNamespace(value_name="response_private_digest")

    coordinator = ResponseCoordinator()
    references = BehaviorMonitorReferenceValues(coordinator)
    input_node_id = config.snapshot.parameters[0].input_node_id
    selected, values = references.resolve_response_source(
        config=config,
        input_node_id=input_node_id,
        operation_key="GET /projects",
        matched_status_code="200",
        media_type="application/json",
        field="body[].id",
    )

    assert values == ["project-a", "project-b"]
    assert coordinator.register_calls == []

    prepared = references.prepare_updates(
        ir=ir,
        config=config,
        updates=[
            InputGeneratorPatch(
                input_node_id=input_node_id,
                strategy=ResponseValueGenerator(
                    type="response_value",
                    value_name="response_private_digest",
                ),
            )
        ],
        selected_reference_provenance=[selected],
    )

    assert len(coordinator.register_calls) == 1
    assert prepared[0].strategy.model_dump(mode="json") == {
        "type": "response_value",
        "value_name": "response_private_digest",
    }
