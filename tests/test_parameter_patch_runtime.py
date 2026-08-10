"""Protect current request-generation state and atomic Parameter Patch Apply."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

import pytest


def _runtime():
    """Create one initialized two-input operation without external services."""
    from restscope.openapi_parser import OpenAPIParser
    from restscope.request_generation import (
        RequestGenerationPatchRuntime,
        RequestGenerationConfigStore,
    )

    ir = OpenAPIParser.parse(
        {
            "openapi": "3.0.3",
            "info": {"title": "Patch runtime", "version": "1"},
            "paths": {
                "/items": {
                    "get": {
                        "parameters": [
                            {
                                "name": "minimum",
                                "in": "query",
                                "schema": {"type": "integer", "minimum": 0, "maximum": 10},
                            },
                            {
                                "name": "maximum",
                                "in": "query",
                                "schema": {"type": "integer", "minimum": 0, "maximum": 10},
                            },
                        ],
                        "responses": {"200": {"description": "ok"}},
                    }
                }
            },
        }
    )
    store = RequestGenerationConfigStore()
    assert store.initialize_once(ir) is True
    return store, RequestGenerationPatchRuntime(store=store, ir_provider=lambda: ir)


def _relational_patch():
    """Return a complete final Generator/Constraint replacement."""
    from restscope.request_generation import SemanticParameterPatch

    return SemanticParameterPatch.model_validate(
        {
            "changes": [
                {
                    "input": "query.minimum",
                    "inclusion_probability": 1,
                    "strategy": {"type": "constant", "value": 2},
                },
                {
                    "input": "query.maximum",
                    "inclusion_probability": 1,
                    "strategy": {"type": "constant", "value": 8},
                },
            ],
            "constraints": [
                {
                    "expression": {
                        "type": "compare",
                        "operator": "<=",
                        "left": {"type": "input_value", "input": "query.minimum"},
                        "right": {"type": "input_value", "input": "query.maximum"},
                    }
                }
            ],
        }
    )


def test_validation_is_deterministic_and_apply_advances_one_revision() -> None:
    """The exact same Patch produces one digest and becomes future state once."""
    from restscope.request_generation.parameter_patch import semantic_state_payload

    store, runtime = _runtime()
    patch = _relational_patch()
    first = runtime.validate(
        operation_key="GET /items",
        expected_revision=0,
        affected_inputs=("query.minimum", "query.maximum"),
        patch=patch,
        seed=17,
        sample_count=5,
    )
    second = runtime.validate(
        operation_key="GET /items",
        expected_revision=0,
        affected_inputs=("query.minimum", "query.maximum"),
        patch=patch,
        seed=17,
        sample_count=5,
    )
    assert first.validation_digest == second.validation_digest
    assert first.samples == second.samples
    assert all(sample["values"] == {"query.minimum": 2, "query.maximum": 8} for sample in first.samples)

    result = runtime.apply(
        operation_key="GET /items",
        expected_revision=0,
        validation_digest=first.validation_digest,
        affected_inputs=("query.minimum", "query.maximum"),
        patch=patch,
        seed=17,
        sample_count=5,
    )
    applied = result.state
    validated = result.validated
    assert result.final_reference_bindings == ()
    assert applied.revision == 1
    assert applied.last_applied_validation_digest == validated.validation_digest

    minimum_state = semantic_state_payload(applied, ("query.minimum",))
    assert minimum_state["additional_constraint_inputs"] == ["query.maximum"]
    assert len(minimum_state["constraints"]) == 1


def test_digest_mismatch_and_no_change_leave_store_untouched() -> None:
    """Failed Apply attempts never increment revision or replace content."""
    from restscope.request_generation import SemanticParameterPatch
    from restscope.request_generation.parameter_patch import ParameterPatchValidationError
    from restscope.request_generation.store import GeneratorConfigError

    store, runtime = _runtime()
    patch = _relational_patch()
    validated = runtime.validate(
        operation_key="GET /items",
        expected_revision=0,
        affected_inputs=("query.minimum", "query.maximum"),
        patch=patch,
    )
    with pytest.raises(ParameterPatchValidationError, match="differ"):
        runtime.apply(
            operation_key="GET /items",
            expected_revision=0,
            validation_digest="0" * 64,
            affected_inputs=("query.minimum", "query.maximum"),
            patch=patch,
        )
    assert store.require_state("GET /items").revision == 0

    current = store.require_state("GET /items")
    with pytest.raises(GeneratorConfigError, match="does not change"):
        runtime.apply(
            operation_key="GET /items",
            expected_revision=0,
            validation_digest=runtime.validate(
                operation_key="GET /items",
                expected_revision=0,
                affected_inputs=("query.minimum",),
                patch=SemanticParameterPatch(),
            ).validation_digest,
            affected_inputs=("query.minimum",),
            patch=SemanticParameterPatch(),
        )
    assert store.require_state("GET /items") == current
    assert validated.expected_revision == 0


def test_two_concurrent_applies_of_one_revision_have_one_winner() -> None:
    """The operation lock turns the second old-revision Apply into a conflict."""
    from restscope.request_generation.parameter_patch import ParameterPatchValidationError
    from restscope.request_generation.store import GeneratorConfigError

    store, runtime = _runtime()
    patch = _relational_patch()
    validated = runtime.validate(
        operation_key="GET /items",
        expected_revision=0,
        affected_inputs=("query.minimum", "query.maximum"),
        patch=patch,
    )

    def apply_once() -> str:
        try:
            runtime.apply(
                operation_key="GET /items",
                expected_revision=0,
                validation_digest=validated.validation_digest,
                affected_inputs=("query.minimum", "query.maximum"),
                patch=patch,
            )
        except (GeneratorConfigError, ParameterPatchValidationError) as exc:
            return exc.code
        return "applied"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = sorted(executor.map(lambda _index: apply_once(), range(2)))
    assert outcomes == ["applied", "request_generation_state_conflict"]
    assert store.require_state("GET /items").revision == 1


def test_source_only_replacement_changes_digest_and_commit_failure_rolls_back() -> None:
    """Source identity is state, and a failed durable commit restores that state."""
    from restscope.operation_references import ResponseFieldReference
    from restscope.request_generation.store import ReferenceValueBinding
    from restscope.request_generation.parameter_patch import SemanticParameterPatch
    from restscope.request_generation.parameter_patch.models import (
        SelectedReferenceProvenance,
    )
    from restscope.request_generation.reference_values import StagedReferenceUpdate

    class ReferenceEvidence:
        """Model a staged database transaction whose commit can fail on exit."""

        fail_commit = False

        def values_for(self, _strategy):
            return [3]

        def resolve_response_source(self, *, input_node_id, field, **_arguments):
            selector = ResponseFieldReference.from_handle(field).selector
            return (
                SelectedReferenceProvenance(
                    input_node_id=input_node_id,
                    kind="response_value",
                    value_name="response_consumer_value",
                    compatible_scalar_type="integer",
                    value_count=1,
                    producer_operation_keys=["GET /producer"],
                    producer_status_code="200",
                    producer_media_type="application/json",
                    source_field=field,
                    source_selector=selector,
                ),
                [3],
            )

        @contextmanager
        def stage_updates(
            self,
            *,
            updates,
            selected_reference_provenance,
            **_arguments,
        ):
            selected = selected_reference_provenance[0]
            yield StagedReferenceUpdate(
                updates=tuple(updates),
                bindings=(
                    ReferenceValueBinding(
                        input_node_id=selected.input_node_id,
                        kind="response_value",
                        value_name=selected.value_name,
                        producer_operation_key=selected.producer_operation_keys[0],
                        producer_status_code=selected.producer_status_code,
                        producer_media_type=selected.producer_media_type,
                        source_field=selected.source_field,
                        source_selector=selected.source_selector,
                    ),
                ),
                removed_response_value_inputs=(),
            )
            if self.fail_commit:
                raise RuntimeError("database commit failed")

    store, original_runtime = _runtime()
    evidence = ReferenceEvidence()
    runtime = type(original_runtime)(
        store=store,
        ir_provider=original_runtime._ir_provider,
        reference_values=evidence,
    )

    def response_patch(field: str) -> SemanticParameterPatch:
        return SemanticParameterPatch.model_validate(
            {
                "changes": [
                    {
                        "input": "query.minimum",
                        "inclusion_probability": 1,
                        "strategy": {
                            "type": "response_value",
                            "source": {
                                "operation_key": "GET /producer",
                                "matched_status_code": "200",
                                "media_type": "application/json",
                                "field": field,
                            },
                        },
                    }
                ]
            }
        )

    first_patch = response_patch("body.old")
    first_validation = runtime.validate(
        operation_key="GET /items",
        expected_revision=0,
        affected_inputs=("query.minimum",),
        patch=first_patch,
    )
    first = runtime.apply(
        operation_key="GET /items",
        expected_revision=0,
        validation_digest=first_validation.validation_digest,
        affected_inputs=("query.minimum",),
        patch=first_patch,
    ).state
    assert first.reference_bindings[0].source_field == "body.old"

    second_patch = response_patch("body.new")
    second_validation = runtime.validate(
        operation_key="GET /items",
        expected_revision=1,
        affected_inputs=("query.minimum",),
        patch=second_patch,
    )
    evidence.fail_commit = True
    with pytest.raises(RuntimeError, match="database commit failed"):
        runtime.apply(
            operation_key="GET /items",
            expected_revision=1,
            validation_digest=second_validation.validation_digest,
            affected_inputs=("query.minimum",),
            patch=second_patch,
        )
    assert store.require_state("GET /items") == first

    evidence.fail_commit = False
    second = runtime.apply(
        operation_key="GET /items",
        expected_revision=1,
        validation_digest=second_validation.validation_digest,
        affected_inputs=("query.minimum",),
        patch=second_patch,
    ).state
    assert second.revision == 2
    assert second.state_digest != first.state_digest
    assert second.reference_bindings[0].source_field == "body.new"
    projected = runtime.read_state(
        operation_key="GET /items",
        input_handles=("query.minimum",),
    )
    strategy = projected["inputs"][0]["generator"]["strategy"]
    assert strategy == {
        "type": "response_value",
        "source": {
            "operation_key": "GET /producer",
            "matched_status_code": "200",
            "media_type": "application/json",
            "field": "body.new",
        },
    }
    assert "response_consumer_value" not in str(projected)


def test_get_state_fails_instead_of_truncating_a_large_constraint_closure() -> None:
    """A model never receives an incomplete active Constraint set."""
    from restscope.request_generation.parameter_patch import semantic_state_payload
    from restscope.request_generation.store import RequestGenerationState

    store, _runtime_value = _runtime()
    current = store.require_state("GET /items")
    huge = RequestGenerationState(
        config=current.config,
        constraints=current.constraints,
        revision=current.revision,
        state_digest=current.state_digest,
        last_applied_validation_digest="x" * 25_000,
        reference_bindings=current.reference_bindings,
    )
    with pytest.raises(ValueError, match="24000"):
        semantic_state_payload(huge, ("query.minimum",))
