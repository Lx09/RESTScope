"""Scenarios for ordered, path-backed Resource Identifier definitions."""

from __future__ import annotations

from pathlib import Path
from contextlib import contextmanager


def test_identifier_contract_accepts_fields_in_full_path_placeholder_order() -> None:
    """A composite result binds every placeholder in the selected full path."""
    from restscope.agent import SystemAgentTask
    from restscope.api_behavior_monitor.resource_identifiers.prompts import (
        IdentifierSelectionDecision,
        validate_identifier_system_output,
    )

    task = SystemAgentTask(
        objective="Choose an identifier.",
        allowed_result_aliases=("I1", "I2"),
        allowed_result_paths=("/assignments/{employeeId}/{projectId}",),
    )
    output = IdentifierSelectionDecision.model_validate(
        {
            "identifier": {
                "path": "/assignments/{employeeId}/{projectId}",
                "fields": ["I1", "I2"],
            }
        }
    )

    assert validate_identifier_system_output(output, task) == ()


def test_identifier_contract_rejects_partial_composite_selection() -> None:
    """A selected path cannot silently drop one component of a composite key."""
    from restscope.agent import SystemAgentTask
    from restscope.api_behavior_monitor.resource_identifiers.prompts import (
        IdentifierSelectionDecision,
        validate_identifier_system_output,
    )

    task = SystemAgentTask(
        objective="Choose an identifier.",
        allowed_result_aliases=("I1", "I2"),
        allowed_result_paths=("/assignments/{employeeId}/{projectId}",),
    )
    output = IdentifierSelectionDecision.model_validate(
        {
            "identifier": {
                "path": "/assignments/{employeeId}/{projectId}",
                "fields": ["I1"],
            }
        }
    )

    errors = validate_identifier_system_output(output, task)

    assert errors == (
        "Selected path requires 2 ordered fields for employeeId, projectId; received 1.",
    )


def test_wrapper_array_is_not_a_resource_group(tmp_path: Path) -> None:
    """Only the wrapper's own direct scalar fields are candidates."""
    from tests.test_resource_identifier_tracker import StubLLMClient, _observation, _tracker

    client = StubLLMClient({"identifier": None})
    tracker, _catalog = _tracker(tmp_path, client)

    result = tracker.observe(
        _observation(body={"data": [{"id": 7}], "count": 1})
    )

    assert result.status == "ignored"
    assert len(client.requests) == 1
    prompt = client.requests[0].messages[1].content
    assert 'field: "count"' in prompt
    assert 'field: "id"' not in prompt
    assert 'response group: "root"' in prompt


def test_root_array_persists_complete_composite_records(tmp_path: Path) -> None:
    """Components observed in one item remain one ordered Identifier Record."""
    from restscope.api_behavior_monitor import ResourceLookupRequest
    from tests.test_resource_identifier_tracker import StubLLMClient, _observation, _tracker

    path = "/memberships/{organizationId}/{userId}"
    client = StubLLMClient(
        {"identifier": {"path": path, "fields": ["I1", "I2"]}}
    )
    tracker, catalog = _tracker(tmp_path, client)
    observation = _observation(
        operation_key="GET /memberships",
        method="GET",
        path="/memberships",
        body=[
            {"organization_id": "org-1", "user_id": 10},
            {"organization_id": "org-2", "user_id": 20},
        ],
    ).model_copy(update={"related_identifier_paths": (path,)})

    result = tracker.observe(observation)
    lookup = catalog.lookup(ResourceLookupRequest(resource="membership"))

    assert result.identifiers_recorded == 2
    assert [
        [(component.name, component.value) for component in record.components]
        for record in lookup.identifiers
    ] == [
        [("organizationId", "org-1"), ("userId", 10)],
        [("organizationId", "org-2"), ("userId", 20)],
    ]


def test_parameter_patch_generates_components_from_one_record() -> None:
    """A complete composite Patch never combines components from different rows."""
    from restscope.openapi_parser import OpenAPIParser
    from restscope.request_generation import (
        RequestGenerationConfigStore,
        RequestGenerationPatchRuntime,
        SemanticParameterPatch,
    )
    from restscope.request_generation.generation import generate_test_case

    class Records:
        """Expose two deliberately distinguishable Identifier Records."""

        rows = (
            {"organizationId": "org-1", "userId": 10},
            {"organizationId": "org-2", "userId": 20},
        )

        def identifier_records(self, *, resource: str, identifier: str):
            assert (resource, identifier) == ("membership", "organizationId/userId")
            return self.rows

        def values_for(self, strategy):
            return [row[strategy.component] for row in self.rows]

        @contextmanager
        def stage_updates(self, *, updates, **_arguments):
            from restscope.request_generation.reference_values import StagedReferenceUpdate
            from restscope.request_generation.store import ReferenceValueBinding

            yield StagedReferenceUpdate(
                updates=tuple(updates),
                bindings=tuple(sorted((
                    ReferenceValueBinding(
                        input_node_id=update.input_node_id,
                        kind="resource_identifier",
                        value_name=update.strategy.resource,
                        identifier=update.strategy.identifier,
                        component=update.strategy.component,
                    )
                    for update in updates
                ), key=lambda item: item.input_node_id)),
                removed_response_value_inputs=(),
            )

    class Backend:
        """Project the same Definition through the model-facing Resource Tool shape."""

        def list_ids(self, *, resource: str, limit: int):
            del limit
            return {
                "structured": {
                    "status": "found",
                    "canonical_resource": resource,
                    "ids": [
                        {
                            "identifier": "organizationId/userId",
                            "components": [
                                {"name": name, "value": value, "value_type": "integer" if isinstance(value, int) else "string"}
                                for name, value in row.items()
                            ],
                        }
                        for row in Records.rows
                    ],
                }
            }

    ir = OpenAPIParser.parse(
        {
            "openapi": "3.0.3",
            "info": {"title": "Composite", "version": "1"},
            "paths": {
                "/memberships/{organizationId}/{userId}": {
                    "get": {
                        "parameters": [
                            {"name": "organizationId", "in": "path", "required": True, "schema": {"type": "string"}},
                            {"name": "userId", "in": "path", "required": True, "schema": {"type": "integer"}},
                        ],
                        "responses": {"200": {"description": "ok"}},
                    }
                }
            },
        }
    )
    store = RequestGenerationConfigStore()
    store.initialize_once(ir)
    records = Records()
    runtime = RequestGenerationPatchRuntime(
        store=store,
        ir_provider=lambda: ir,
        reference_values=records,
        resource_backend=Backend(),
    )
    strategy = lambda component: {
        "type": "resource_identifier",
        "resource": "membership",
        "identifier": "organizationId/userId",
        "component": component,
    }
    patch = SemanticParameterPatch.model_validate(
        {
            "changes": [
                {"input": "path.organizationId", "inclusion_probability": 1, "strategy": strategy("organizationId")},
                {"input": "path.userId", "inclusion_probability": 1, "strategy": strategy("userId")},
            ],
            # Only one component is constrained. The solver must still move
            # the other component with it as one complete Record.
            "constraints": [
                {
                    "expression": {
                        "type": "compare",
                        "operator": "==",
                        "left": {
                            "type": "input_value",
                            "input": "path.organizationId",
                        },
                        "right": {"type": "literal", "value": "org-2"},
                    }
                }
            ],
        }
    )
    validated = runtime.validate(
        operation_key="GET /memberships/{organizationId}/{userId}",
        expected_revision=0,
        affected_inputs=("path.organizationId", "path.userId"),
        patch=patch,
    )
    assert all(
        sample["values"]
        == {"path.organizationId": "org-2", "path.userId": 20}
        for sample in validated.samples
    )
    applied = runtime.apply(
        operation_key="GET /memberships/{organizationId}/{userId}",
        expected_revision=0,
        validation_digest=validated.validation_digest,
        affected_inputs=("path.organizationId", "path.userId"),
        patch=patch,
    ).state

    generated = [
        generate_test_case(
            applied.config.snapshot,
            applied.config,
            run_seed=91,
            case_index=index,
            reference_values=records,
        ).path_parameters
        for index in range(10)
    ]

    assert all(
        (item["organizationId"], item["userId"])
        in {("org-1", 10), ("org-2", 20)}
        for item in generated
    )


def test_single_component_identifier_may_bind_a_query_parameter() -> None:
    """Only composite Definitions have the path-only complete-Patch rule."""
    from restscope.openapi_parser import OpenAPIParser
    from restscope.request_generation import (
        RequestGenerationConfigStore,
        RequestGenerationPatchRuntime,
        SemanticParameterPatch,
    )

    class Records:
        """Expose one ordinary single-component Identifier Definition."""

        def identifier_records(self, *, resource: str, identifier: str):
            assert (resource, identifier) == ("user", "userId")
            return ({"userId": 7},)

        def values_for(self, strategy):
            assert strategy.component == "userId"
            return [7]

    class Backend:
        """Return the same Definition through the Resource Tool boundary."""

        def list_ids(self, *, resource: str, limit: int):
            del limit
            return {
                "structured": {
                    "status": "found",
                    "canonical_resource": resource,
                    "ids": [
                        {
                            "identifier": "userId",
                            "components": [
                                {
                                    "name": "userId",
                                    "value": 7,
                                    "value_type": "integer",
                                }
                            ],
                        }
                    ],
                }
            }

    ir = OpenAPIParser.parse(
        {
            "openapi": "3.0.3",
            "info": {"title": "Single identifier", "version": "1"},
            "paths": {
                "/search": {
                    "get": {
                        "parameters": [
                            {
                                "name": "userId",
                                "in": "query",
                                "schema": {"type": "integer"},
                            }
                        ],
                        "responses": {"200": {"description": "ok"}},
                    }
                }
            },
        }
    )
    store = RequestGenerationConfigStore()
    store.initialize_once(ir)
    runtime = RequestGenerationPatchRuntime(
        store=store,
        ir_provider=lambda: ir,
        reference_values=Records(),
        resource_backend=Backend(),
    )
    patch = SemanticParameterPatch.model_validate(
        {
            "changes": [
                {
                    "input": "query.userId",
                    "inclusion_probability": 1,
                    "strategy": {
                        "type": "resource_identifier",
                        "resource": "user",
                        "identifier": "userId",
                        "component": "userId",
                    },
                }
            ]
        }
    )

    validated = runtime.validate(
        operation_key="GET /search",
        expected_revision=0,
        affected_inputs=("query.userId",),
        patch=patch,
    )

    assert validated.validation_digest
