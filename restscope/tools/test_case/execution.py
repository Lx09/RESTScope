"""Validate one Test Case query and bound its model-visible result."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from pydantic import ValidationError

from restscope.tools.runtime import ToolFailure

from .contracts import _InputT
from .presentation import _bound_catalog_result


def _run_catalog_query(
    *,
    model_type: type[_InputT],
    arguments: dict[str, Any],
    execute: Callable[[_InputT], dict[str, Any]],
) -> dict[str, Any]:
    """Validate one fixed query contract and bound its model-visible result."""
    try:
        query = model_type.model_validate(arguments)
        return _bound_catalog_result(execute(query))
    except (ValidationError, KeyError, ValueError) as exc:
        raise ToolFailure(
            code="invalid_test_case_query",
            message=str(exc),
        ) from exc
