"""Shared pytest setup that isolates environment variables and other process-wide state between RESTScope scenarios."""

from __future__ import annotations

import pytest


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
