"""Protect current request-generation state and atomic Parameter Patch Apply."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
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
                        "responses": {
                            "200": {
                                "description": "ok",
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "type": "object",
                                            "properties": {
                                                "old": {"type": "integer"},
                                                "new": {"type": "integer"},
                                            },
                                        }
                                    }
                                },
                            }
                        },
                    }
                },
                "/producer": {
                    "get": {
                        "responses": {
                            "200": {
                                "description": "ok",
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "type": "object",
                                            "properties": {
                                                "old": {"type": "integer"},
                                                "new": {"type": "integer"},
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
    store = RequestGenerationConfigStore()
    assert store.initialize_once(ir) is True
    return store, RequestGenerationPatchRuntime(store=store, ir_provider=lambda: ir)


def _relational_patch():
    """Return a complete final Generator/Constraint replacement."""
    from restscope.request_generation.parameter_patch import SemanticParameterPatch

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
    from restscope.request_generation.parameter_patch import SemanticParameterPatch
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
    from datetime import UTC, datetime

    from restscope.api_behavior_monitor.catalog import (
        APIBehaviorCatalog,
        ObservationWrite,
        OperationDefinition,
    )
    from restscope.db import (
        Base,
        SqlAlchemyAPIBehaviorUnitOfWork,
        create_engine_from_url,
        make_session_factory,
    )
    from restscope.request_generation import BehaviorMonitorReferences
    from restscope.request_generation.parameter_patch import (
        ParameterPatchValidationError,
        SemanticParameterPatch,
    )
    engine = create_engine_from_url("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    sessions = make_session_factory(engine)

    class CommitControlledUnitOfWork(SqlAlchemyAPIBehaviorUnitOfWork):
        """Fail only the explicitly selected staged binding commit."""

        fail_commit = False

        def commit(self) -> None:
            if self.fail_commit:
                raise RuntimeError("database commit failed")
            super().commit()

    catalog = APIBehaviorCatalog(lambda: CommitControlledUnitOfWork(sessions))
    catalog.ensure_operation(
        OperationDefinition(
            operation_id="GET /producer",
            method="GET",
            path="/producer",
        )
    )
    catalog.record_observation(
        ObservationWrite(
            operation_id="GET /producer",
            timestamp=datetime(2026, 8, 12, tzinfo=UTC),
            status_code=200,
            media_type="application/json",
            request_json={"path": "/producer"},
            response_json='{"old":3,"new":4}',
        )
    )

    store, original_runtime = _runtime()
    references = BehaviorMonitorReferences(catalog)
    runtime = type(original_runtime)(
        store=store,
        ir_provider=original_runtime._ir_provider,
        references=references,
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
                                "status_code": 200,
                                "media_type": "application/json",
                                "field": field,
                            },
                        },
                    }
                ]
            }
        )

    unavailable_runtime = type(original_runtime)(
        store=store,
        ir_provider=original_runtime._ir_provider,
    )
    unavailable_patch = response_patch("body.old")
    with pytest.raises(
        ParameterPatchValidationError,
        match="Response Value evidence is unavailable",
    ):
        unavailable_runtime.validate(
            operation_key="GET /items",
            expected_revision=0,
            affected_inputs=("query.minimum",),
            patch=unavailable_patch,
        )
    assert store.require_state("GET /items").revision == 0

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
    assert first.reference_bindings[0].selector == "$.old"

    remove_reference_patch = SemanticParameterPatch.model_validate(
        {
            "changes": [
                {
                    "input": "query.minimum",
                    "inclusion_probability": 1,
                    "strategy": {"type": "constant", "value": 5},
                }
            ]
        }
    )
    removal_validation = unavailable_runtime.validate(
        operation_key="GET /items",
        expected_revision=1,
        affected_inputs=("query.minimum",),
        patch=remove_reference_patch,
    )
    with pytest.raises(
        ParameterPatchValidationError,
        match="Reference-backed Patch application is unavailable",
    ):
        unavailable_runtime.apply(
            operation_key="GET /items",
            expected_revision=1,
            validation_digest=removal_validation.validation_digest,
            affected_inputs=("query.minimum",),
            patch=remove_reference_patch,
        )
    assert store.require_state("GET /items") == first

    second_patch = response_patch("body.new")
    second_validation = runtime.validate(
        operation_key="GET /items",
        expected_revision=1,
        affected_inputs=("query.minimum",),
        patch=second_patch,
    )
    CommitControlledUnitOfWork.fail_commit = True
    with pytest.raises(RuntimeError, match="database commit failed"):
        runtime.apply(
            operation_key="GET /items",
            expected_revision=1,
            validation_digest=second_validation.validation_digest,
            affected_inputs=("query.minimum",),
            patch=second_patch,
        )
    assert store.require_state("GET /items") == first

    CommitControlledUnitOfWork.fail_commit = False
    second = runtime.apply(
        operation_key="GET /items",
        expected_revision=1,
        validation_digest=second_validation.validation_digest,
        affected_inputs=("query.minimum",),
        patch=second_patch,
    ).state
    assert second.revision == 2
    assert second.state_digest != first.state_digest
    assert second.reference_bindings[0].selector == "$.new"
    projected = runtime.read_state(
        operation_key="GET /items",
        input_handles=("query.minimum",),
    )
    strategy = projected["inputs"][0]["generator"]["strategy"]
    assert strategy == {
        "type": "response_value",
        "source": {
            "operation_key": "GET /producer",
            "status_code": 200,
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
