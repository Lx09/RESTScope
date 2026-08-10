"""Protect normalized OpenAPI export and append-only response-change auditing."""

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
    """Initialization stores all operations; one real change appends one audit event."""

    from restscope import RESTScopeApp
    from restscope.openapi_parser import OpenAPIParser
    from restscope.config import DBConfig, RESTScopeConfig

    config = replace(
        RESTScopeConfig.from_environment(),
        db=DBConfig(url=f"sqlite:///{tmp_path / 'audit.sqlite'}"),
    )
    app = RESTScopeApp.from_config(config)
    try:
        context = app.initialize(
            schema_source={
                "kind": "inline",
                "format": "json",
                "content": json.dumps(_spec()),
            }
        )
        initial = app.export_current_openapi()
        assert set(OpenAPIParser.parse(initial).operations) == {"POST /items"}
        assert app.list_openapi_change_events() == []

        tracker = app.api_behavior_monitor_coordinator.contract_tracker
        changed = tracker.observe(
            ir=context.ir,
            operation_key="POST /items",
            status_code=201,
            media_type="application/json",
            body=b'{"id": 7}',
        )
        repeated = tracker.observe(
            ir=context.ir,
            operation_key="POST /items",
            status_code=201,
            media_type="application/json",
            body=b'{"id": 7}',
        )

        events = app.list_openapi_change_events("POST /items")
        assert changed.status == "updated"
        assert repeated.status == "already_checked"
        assert len(events) == 1
        assert events[0].response_before is None
        assert events[0].response_after["content"]["application/json"]["schema"]
        assert "201" in app.export_current_openapi()["paths"]["/items"]["post"]["responses"]
        assert set(OpenAPIParser.parse(app.export_current_openapi()).operations) == {
            "POST /items"
        }
    finally:
        app.close()


def test_catalog_rolls_back_current_document_when_event_write_fails(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """A transaction failure cannot leave current OpenAPI without its audit event."""

    from restscope.openapi_audit import OpenAPIChangeEventWrite, OpenAPIAudit
    from restscope.db import (
        Base,
        SqlAlchemyOpenAPIUnitOfWork,
        create_engine_from_url,
        make_session_factory,
    )
    from restscope.db.adapters.openapi_audit import SqlAlchemyOpenAPIRepository

    engine = create_engine_from_url(f"sqlite:///{tmp_path / 'rollback.sqlite'}")
    Base.metadata.create_all(engine)
    catalog = OpenAPIAudit(
        lambda: SqlAlchemyOpenAPIUnitOfWork(make_session_factory(engine))
    )
    catalog.initialize(_spec())
    original = SqlAlchemyOpenAPIRepository.record_change

    def fail_after_flush(self, **arguments):
        original(self, **arguments)
        raise RuntimeError("simulated event failure")

    monkeypatch.setattr(SqlAlchemyOpenAPIRepository, "record_change", fail_after_flush)
    changed = deepcopy(_spec())
    changed["paths"]["/items"]["post"]["responses"]["201"] = {
        "description": "created"
    }

    with pytest.raises(RuntimeError, match="simulated event failure"):
        catalog.record_change(
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

    assert catalog.current_document() == _spec()
    assert catalog.list_changes() == []


def test_tracker_restores_ir_and_retry_state_when_catalog_write_fails() -> None:
    """A failed durable update leaves the in-memory Response exactly retryable."""

    from restscope.api_behavior_monitor.response_contracts import ResponseContractTracker
    from restscope.openapi_parser import OpenAPIParser, build_openapi_document

    class FailingCatalog:
        def record_change(self, **_arguments):
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
    tracker.audit = None
    retried = tracker.observe(
        ir=ir,
        operation_key="POST /items",
        status_code=201,
        media_type="application/json",
        body=b'{"id": 7}',
    )
    assert retried.status == "updated"
