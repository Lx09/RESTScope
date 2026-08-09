"""Protect current-only Generator persistence without operation snapshots."""

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
    from restscope.request_generation import RequestGenerationConfigStore

    ir = OpenAPIParser.parse(
        {
            "openapi": "3.0.3",
            "info": {"title": "Current", "version": "1"},
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
    engine = create_engine_from_url(f"sqlite:///{tmp_path / 'current.sqlite'}")
    Base.metadata.create_all(engine)
    session_factory = make_session_factory(engine)
    catalog = RequestGenerationConfigStore(
        lambda: SqlAlchemyGeneratorConfigUnitOfWork(session_factory)
    )
    assert catalog.initialize_once(ir) is True
    return catalog, engine


def test_current_write_replaces_only_the_input_row(tmp_path: Path) -> None:
    """An accepted content write leaves one row and no snapshot/history tables."""

    from sqlalchemy import inspect, text

    from restscope.request_generation import InputGeneratorPatch, prepare_accepted_generator_patch

    catalog, engine = _catalog(tmp_path)
    current = catalog.require_operation("GET /items/{itemId}")
    updated = prepare_accepted_generator_patch(
        current,
        [
            InputGeneratorPatch(
                input_node_id=current.configs[0].input_node_id,
                strategy={"type": "constant", "value": "known-item"},
            )
        ],
    )
    with catalog.unit_of_work_factory() as uow:
        uow.generator_configs.replace_inputs(
            operation_key=current.operation_key,
            expected=current.configs,
            updated=updated.configs,
        )
        uow.commit()

    rebuilt = catalog.require_operation(current.operation_key)
    with engine.connect() as connection:
        row_count = connection.scalar(
            text("SELECT COUNT(*) FROM input_generator_configs")
        )
    assert rebuilt.configs[0].strategy.value == "known-item"
    assert rebuilt.snapshot == current.snapshot
    assert row_count == 1
    assert "operation_generator_configs" not in inspect(engine).get_table_names()
    assert "generator_config_revisions" not in inspect(engine).get_table_names()


def test_current_content_compare_rejects_a_stale_writer(tmp_path: Path) -> None:
    """Optimistic locking compares exact current rows instead of an integer revision."""

    import pytest

    from restscope.request_generation import InputGeneratorPatch, prepare_accepted_generator_patch
    from restscope.request_generation.ports import GeneratorConfigConcurrentWrite

    catalog, _ = _catalog(tmp_path)
    current = catalog.require_operation("GET /items/{itemId}")
    updated = prepare_accepted_generator_patch(
        current,
        [
            InputGeneratorPatch(
                input_node_id=current.configs[0].input_node_id,
                strategy={"type": "constant", "value": "first"},
            )
        ],
    )
    with catalog.unit_of_work_factory() as first:
        first.generator_configs.replace_inputs(
            operation_key=current.operation_key,
            expected=current.configs,
            updated=updated.configs,
        )
        first.commit()

    with catalog.unit_of_work_factory() as stale:
        with pytest.raises(GeneratorConfigConcurrentWrite):
            stale.generator_configs.replace_inputs(
                operation_key=current.operation_key,
                expected=current.configs,
                updated=updated.configs,
            )
