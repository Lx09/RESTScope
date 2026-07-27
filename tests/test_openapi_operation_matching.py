"""Regression scenarios for openapi operation matching. Each test documents one observable contract or failure boundary."""

from __future__ import annotations

import pytest


def _ir():
    from restscope.openapi_parser import OpenAPIParser

    return OpenAPIParser.parse(
        {
            "openapi": "3.0.3",
            "info": {"title": "matching", "version": "1"},
            "paths": {
                "/users/me": {
                    "get": {"responses": {"200": {"description": "ok"}}}
                },
                "/users/{userId}": {
                    "get": {"responses": {"200": {"description": "ok"}}}
                },
                "/{collection}/me": {
                    "get": {"responses": {"200": {"description": "ok"}}}
                },
                "/teams/{member}": {
                    "get": {"responses": {"200": {"description": "ok"}}}
                },
            },
        }
    )


def test_match_operation_prefers_exact_openapi_path() -> None:
    """Scenario: verify that match operation prefers exact openapi path."""
    from restscope.openapi_parser import match_operation

    operation = match_operation(_ir(), method="get", path="/users/me")

    assert operation.operation_key == "GET /users/me"


def test_match_operation_resolves_one_concrete_path_segment() -> None:
    """Scenario: verify that match operation resolves one concrete path segment."""
    from restscope.openapi_parser import match_operation

    operation = match_operation(_ir(), method="GET", path="/users/42")

    assert operation.operation_key == "GET /users/{userId}"


def test_match_operation_rejects_ambiguous_template_matches() -> None:
    """Scenario: verify that match operation rejects ambiguous template matches."""
    from restscope.openapi_parser import OpenAPIOperationMatchError, match_operation

    with pytest.raises(OpenAPIOperationMatchError) as raised:
        match_operation(_ir(), method="GET", path="/teams/me")

    assert raised.value.code == "operation_match_ambiguous"
    assert raised.value.operation_keys == (
        "GET /teams/{member}",
        "GET /{collection}/me",
    )


def test_match_operation_reports_no_match() -> None:
    """Scenario: verify that match operation reports no match."""
    from restscope.openapi_parser import OpenAPIOperationMatchError, match_operation

    with pytest.raises(OpenAPIOperationMatchError) as raised:
        match_operation(_ir(), method="POST", path="/users/42")

    assert raised.value.code == "operation_match_not_found"
