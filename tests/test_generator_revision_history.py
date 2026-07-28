"""Candidate transaction tests for persisted Generator configurations.

These scenarios protect only the lifecycle used by Operation Smoke: stage one
complete candidate, then either accept all of it or restore its complete parent.
They intentionally do not expose revision-history browsing or partial acceptance.
"""

from __future__ import annotations

from pathlib import Path


def _catalog(tmp_path: Path):
    """Create an initialized catalog with one required and one optional input."""
    from restscope.db import (
        Base,
        SqlAlchemyGeneratorConfigUnitOfWork,
        create_engine_from_url,
        make_session_factory,
    )
    from restscope.openapi_parser import OpenAPIParser
    from restscope.testing import GeneratorConfigCatalog

    ir = OpenAPIParser.parse(
        {
            "openapi": "3.0.3",
            "info": {"title": "History", "version": "1"},
            "paths": {
                "/items/{itemId}": {
                    "get": {
                        "parameters": [
                            {
                                "name": "itemId",
                                "in": "path",
                                "required": True,
                                "schema": {"type": "string"},
                            },
                            {
                                "name": "region",
                                "in": "query",
                                "required": False,
                                "schema": {"type": "string"},
                            },
                        ],
                        "responses": {"200": {"description": "ok"}},
                    }
                }
            },
        }
    )
    engine = create_engine_from_url(
        f"sqlite:///{tmp_path / 'history.sqlite'}"
    )
    Base.metadata.create_all(engine)
    catalog = GeneratorConfigCatalog(
        lambda: SqlAlchemyGeneratorConfigUnitOfWork(
            make_session_factory(engine)
        )
    )
    assert catalog.initialize_once(ir) is True
    return catalog, ir.operations["GET /items/{itemId}"]


def _stage_known_item(catalog, operation_key: str):
    """Stage the representative whole Generator candidate used by each test."""
    current = catalog.require_operation(operation_key)
    return catalog.stage_candidate(
        operation_key=operation_key,
        expected_revision=current.revision,
        updates=[
            {
                "input_node_id": current.configs[0].input_node_id,
                "strategy": {"type": "constant", "value": "known-item"},
            }
        ],
        hypothesis={"kind": "operation_smoke_todo_patch"},
    )


def test_candidate_revision_is_accepted_as_one_complete_configuration(
    tmp_path: Path,
) -> None:
    """Accepting a candidate keeps every staged change and returns that config."""
    catalog, operation = _catalog(tmp_path)
    candidate = _stage_known_item(catalog, operation.operation_key)

    accepted = catalog.accept_candidate(
        operation_key=operation.operation_key,
        candidate_revision=candidate.revision,
        evaluation={
            "validation_status": "accepted",
            "success_rate": 0.9,
        },
    )

    assert accepted == candidate
    assert accepted.configs[0].strategy.value == "known-item"
    assert catalog.require_operation(operation.operation_key) == accepted


def test_rejected_candidate_restores_the_complete_parent_configuration(
    tmp_path: Path,
) -> None:
    """Rejecting any candidate effect rolls back every staged input change."""
    catalog, operation = _catalog(tmp_path)
    baseline = catalog.require_operation(operation.operation_key)
    candidate = _stage_known_item(catalog, operation.operation_key)

    restored = catalog.reject_candidate_and_rollback(
        operation_key=operation.operation_key,
        candidate_revision=candidate.revision,
        evaluation={
            "validation_status": "rejected",
            "success_rate": 0.1,
        },
    )

    assert restored.revision == candidate.revision + 1
    assert restored.configs == baseline.configs
    assert catalog.require_operation(operation.operation_key) == restored


def test_interrupted_candidate_is_automatically_rolled_back(
    tmp_path: Path,
) -> None:
    """Startup recovery cannot leave an unevaluated candidate executable."""
    catalog, operation = _catalog(tmp_path)
    baseline = catalog.require_operation(operation.operation_key)
    candidate = _stage_known_item(catalog, operation.operation_key)

    recovered = catalog.recover_interrupted_candidate(
        operation.operation_key
    )

    assert recovered.revision == candidate.revision + 1
    assert recovered.configs == baseline.configs
    assert catalog.require_operation(operation.operation_key) == recovered


def test_accept_rejects_a_stale_candidate_revision(tmp_path: Path) -> None:
    """An old candidate number cannot accept a newer current configuration."""
    import pytest

    from restscope.testing import GeneratorConfigRevisionConflict

    catalog, operation = _catalog(tmp_path)
    candidate = _stage_known_item(catalog, operation.operation_key)

    with pytest.raises(GeneratorConfigRevisionConflict):
        catalog.accept_candidate(
            operation_key=operation.operation_key,
            candidate_revision=candidate.revision - 1,
            evaluation={"validation_status": "accepted"},
        )
