"""Protect the Catalog's OpenAPI export and append-only change auditing."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path

import pytest


def _spec() -> dict:
    """Build one operation whose observed 201 response changes its contract."""

    return {
        "openapi": "3.0.3",
        "info": {"title": "Audit", "version": "1"},
        "paths": {
            "/items": {
                "post": {
                    "responses": {"200": {"description": "ok"}},
                }
            }
        },
    }


def test_app_exports_full_current_document_and_filterable_change_events(
    tmp_path: Path,
) -> None:
    """App initialization exposes the complete normalized persisted document."""

    from restscope import RESTScopeApp
    from restscope.openapi_parser import OpenAPIParser
    from restscope.config import DBConfig, RESTScopeConfig

    config = replace(
        RESTScopeConfig.from_environment(),
        db=DBConfig(url=f"sqlite:///{tmp_path / 'audit.sqlite'}"),
    )
    app = RESTScopeApp.from_config(config)
    try:
        app.initialize(
            schema_source={
                "kind": "inline",
                "format": "json",
                "content": json.dumps(_spec()),
            }
        )
        initial = app.export_current_openapi()
        assert set(OpenAPIParser.parse(initial).operations) == {"POST /items"}
        assert app.list_openapi_change_events() == []
    finally:
        app.close()


def test_contract_tracker_persists_one_filterable_change_event(tmp_path: Path) -> None:
    """The Contract Monitor owns durable change publication and deduplication."""
    from restscope.api_behavior_monitor.catalog import APIBehaviorCatalog
    from restscope.api_behavior_monitor.contract_monitor import ResponseContractTracker
    from restscope.db import (
        Base,
        SqlAlchemyAPIBehaviorUnitOfWork,
        create_engine_from_url,
        make_session_factory,
    )
    from restscope.openapi_parser import OpenAPIParser

    engine = create_engine_from_url(f"sqlite:///{tmp_path / 'tracker.sqlite'}")
    Base.metadata.create_all(engine)
    sessions = make_session_factory(engine)
    catalog = APIBehaviorCatalog(
        lambda: SqlAlchemyAPIBehaviorUnitOfWork(sessions)
    )
    catalog.initialize_api(document=_spec(), operations=[])
    ir = OpenAPIParser.parse(_spec())
    tracker = ResponseContractTracker(catalog)

    changed = tracker.observe(
        ir=ir,
        operation_key="POST /items",
        status_code=201,
        media_type="application/json",
        body=b'{"id": 7}',
    )
    repeated = tracker.observe(
        ir=ir,
        operation_key="POST /items",
        status_code=201,
        media_type="application/json",
        body=b'{"id": 7}',
    )

    events = catalog.list_openapi_changes("POST /items")
    assert changed.status == "updated"
    assert repeated.status == "already_checked"
    assert len(events) == 1
    assert events[0].response_before is None
    assert events[0].response_after["content"]["application/json"]["schema"]
    assert "201" in catalog.current_openapi()["paths"]["/items"]["post"]["responses"]


def test_catalog_rolls_back_current_document_when_event_write_fails(
    tmp_path: Path,
) -> None:
    """A transaction failure cannot leave current OpenAPI without its audit event."""

    from restscope.api_behavior_monitor import APIBehaviorCatalog
    from restscope.api_behavior_monitor.catalog import OpenAPIChangeEventWrite
    from restscope.db import (
        Base,
        SqlAlchemyAPIBehaviorUnitOfWork,
        create_engine_from_url,
        make_session_factory,
    )

    class CommitFailureUnitOfWork(SqlAlchemyAPIBehaviorUnitOfWork):
        """Fail after repository writes so context cleanup must roll them back."""

        def commit(self) -> None:
            """Materialize pending SQL without committing the transaction."""

            if self.session is None:
                raise RuntimeError("Unit of work is not active")
            self.session.flush()
            raise RuntimeError("simulated event failure")

    engine = create_engine_from_url(f"sqlite:///{tmp_path / 'rollback.sqlite'}")
    Base.metadata.create_all(engine)
    sessions = make_session_factory(engine)
    catalog = APIBehaviorCatalog(
        lambda: SqlAlchemyAPIBehaviorUnitOfWork(sessions)
    )
    catalog.initialize_api(document=_spec(), operations=[])
    failing_catalog = APIBehaviorCatalog(lambda: CommitFailureUnitOfWork(sessions))
    changed = deepcopy(_spec())
    changed["paths"]["/items"]["post"]["responses"]["201"] = {
        "description": "created"
    }

    with pytest.raises(RuntimeError, match="simulated event failure"):
        failing_catalog.record_openapi_change(
            document=changed,
            event=OpenAPIChangeEventWrite(
                operation_key="POST /items",
                status_code=201,
                media_type="application/json",
                changes=["response:201"],
                response_before=None,
                response_after={"description": "created"},
            ),
        )

    assert catalog.current_openapi() == _spec()
    assert catalog.list_openapi_changes() == []


def test_tracker_restores_ir_and_retry_state_when_catalog_write_fails() -> None:
    """A failed durable update leaves the in-memory Response exactly retryable."""

    from restscope.api_behavior_monitor.contract_monitor import ResponseContractTracker
    from restscope.openapi_parser import OpenAPIParser, build_openapi_document

    class FailingCatalog:
        def record_openapi_change(self, **_arguments):
            raise RuntimeError("database unavailable")

    ir = OpenAPIParser.parse(_spec())
    before = build_openapi_document(ir, list(ir.operations))
    tracker = ResponseContractTracker(FailingCatalog())

    with pytest.raises(RuntimeError, match="database unavailable"):
        tracker.observe(
            ir=ir,
            operation_key="POST /items",
            status_code=201,
            media_type="application/json",
            body=b'{"id": 7}',
        )

    assert build_openapi_document(ir, list(ir.operations)) == before
    tracker.catalog = None
    retried = tracker.observe(
        ir=ir,
        operation_key="POST /items",
        status_code=201,
        media_type="application/json",
        body=b'{"id": 7}',
    )
    assert retried.status == "updated"
