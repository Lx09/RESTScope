"""Contracts for initial and directly accepted Generator revisions."""

from __future__ import annotations

from pathlib import Path


def _catalog(tmp_path: Path):
    """Create an initialized catalog with one required path input."""
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
                            }
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
    session_factory = make_session_factory(engine)
    catalog = GeneratorConfigCatalog(
        lambda: SqlAlchemyGeneratorConfigUnitOfWork(session_factory)
    )
    assert catalog.initialize_once(ir) is True
    return catalog, session_factory, ir.operations["GET /items/{itemId}"]


def test_direct_patch_appends_one_accepted_revision(tmp_path: Path) -> None:
    """Scenario: applying a Patch has no pending or rollback state."""
    from restscope.db import SqlAlchemyGeneratorConfigUnitOfWork

    catalog, session_factory, operation = _catalog(tmp_path)
    baseline = catalog.require_operation(operation.operation_key)
    accepted = catalog.apply_accepted_patch(
        operation_key=operation.operation_key,
        expected_revision=baseline.revision,
        updates=[
            {
                "input_node_id": baseline.configs[0].input_node_id,
                "strategy": {"type": "constant", "value": "known-item"},
            }
        ],
    )

    with SqlAlchemyGeneratorConfigUnitOfWork(session_factory) as uow:
        initial_revision = uow.generator_configs.get_revision(
            operation.operation_key,
            1,
        )
        accepted_revision = uow.generator_configs.get_revision(
            operation.operation_key,
            2,
        )

    assert accepted.revision == 2
    assert accepted.configs[0].strategy.value == "known-item"
    assert initial_revision is not None
    assert initial_revision.parent_revision is None
    assert accepted_revision is not None
    assert accepted_revision.parent_revision == 1
    assert accepted_revision.config == accepted


def test_direct_patch_rejects_a_stale_expected_revision(tmp_path: Path) -> None:
    """Scenario: optimistic locking protects accepted history from lost writes."""
    import pytest

    from restscope.testing import GeneratorConfigRevisionConflict

    catalog, _, operation = _catalog(tmp_path)
    current = catalog.require_operation(operation.operation_key)

    with pytest.raises(GeneratorConfigRevisionConflict):
        catalog.apply_accepted_patch(
            operation_key=operation.operation_key,
            expected_revision=current.revision + 1,
            updates=[
                {
                    "input_node_id": current.configs[0].input_node_id,
                    "strategy": {"type": "constant", "value": "stale"},
                }
            ],
        )
