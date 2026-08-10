"""Protect current request-generation state and atomic Parameter Patch Apply."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest


def _runtime():
    """Create one initialized two-input operation without external services."""
    from restscope.openapi_parser import OpenAPIParser
    from restscope.request_generation import (
        ParameterPatchRuntime,
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
    return store, ParameterPatchRuntime(store=store, ir_provider=lambda: ir)


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
    from restscope.request_generation.patch_validation import semantic_state_payload

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

    applied, validated, references = runtime.apply(
        operation_key="GET /items",
        expected_revision=0,
        validation_digest=first.validation_digest,
        affected_inputs=("query.minimum", "query.maximum"),
        patch=patch,
        seed=17,
        sample_count=5,
    )
    assert references == []
    assert applied.revision == 1
    assert applied.last_applied_validation_digest == validated.validation_digest

    minimum_state = semantic_state_payload(applied, ("query.minimum",))
    assert minimum_state["additional_constraint_inputs"] == ["query.maximum"]
    assert len(minimum_state["constraints"]) == 1


def test_digest_mismatch_and_no_change_leave_store_untouched() -> None:
    """Failed Apply attempts never increment revision or replace content."""
    from restscope.request_generation import SemanticParameterPatch
    from restscope.request_generation.patch_validation import ParameterPatchValidationError
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
    from restscope.request_generation.patch_validation import ParameterPatchValidationError
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


def test_fallible_apply_preparation_cannot_publish_partial_store_state() -> None:
    """A failed reference-registration callback leaves the complete revision intact."""
    store, _runtime_value = _runtime()
    before = store.require_state("GET /items")

    def fail_before_replace(_state):
        """Represent a transactional response-source registration failure."""
        raise ValueError("registration failed")

    with pytest.raises(ValueError, match="registration failed"):
        store.apply_validated(
            operation_key="GET /items",
            expected_revision=0,
            prepare=fail_before_replace,
        )

    assert store.require_state("GET /items") == before


def test_get_state_fails_instead_of_truncating_a_large_constraint_closure() -> None:
    """A model never receives an incomplete active Constraint set."""
    from restscope.request_generation.patch_validation import semantic_state_payload
    from restscope.request_generation.store import RequestGenerationState

    store, _runtime_value = _runtime()
    current = store.require_state("GET /items")
    huge = RequestGenerationState(
        config=current.config,
        constraints=current.constraints,
        revision=current.revision,
        state_digest=current.state_digest,
        last_applied_validation_digest="x" * 25_000,
    )
    with pytest.raises(ValueError, match="24000"):
        semantic_state_payload(huge, ("query.minimum",))
