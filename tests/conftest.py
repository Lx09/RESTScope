"""Shared pytest setup that isolates environment variables and other process-wide state between RESTScope scenarios."""

from __future__ import annotations

import pytest


@pytest.fixture
def api_behavior_catalog():
    """Provide a real in-memory Catalog for Batch persistence scenarios."""

    from restscope.api_behavior_monitor.catalog import APIBehaviorCatalog
    from restscope.db import (
        Base,
        SqlAlchemyAPIBehaviorUnitOfWork,
        create_engine_from_url,
        make_session_factory,
    )

    engine = create_engine_from_url("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    sessions = make_session_factory(engine)
    return APIBehaviorCatalog(
        lambda: SqlAlchemyAPIBehaviorUnitOfWork(sessions)
    )


@pytest.fixture
def tool_context():
    """Fixture: provide tool context for isolated scenarios."""
    from restscope.tools.context import ToolContext
    from restscope.openapi_parser import OpenAPIParser

    ir = OpenAPIParser.parse(
        {
            "openapi": "3.0.3",
            "info": {"title": "Test Tool Context", "version": "1.0"},
            "paths": {
                "/health": {
                    "get": {"responses": {"200": {"description": "ok"}}}
                }
            },
        }
    )
    return ToolContext(
        ir=ir,
        baseline_schema_source={"kind": "inline", "format": "json", "content": "{}"},
        base_url=None,
        headers={},
    )
