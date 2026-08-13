"""Adapt trusted App context to the six bounded OpenAPI query behaviors.

``OpenAPIToolBackend`` is the stable binding object used by the Harness and
temporary Agents. Each public method delegates to one behavior-specific module;
shared OpenAPI traversal remains private in ``traversal.py``.
"""

from __future__ import annotations

from collections.abc import Callable

from restscope.openapi_parser.ir import OpenAPISpecIR, OperationIR
from restscope.tools.context import ToolContext
from restscope.tools.runtime import ToolFailure

from .input_queries import get_input_schema as query_input_schema
from .input_queries import list_inputs as query_inputs
from .observed_queries import (
    ObservedResponseReader,
)
from .observed_queries import (
    find_observed_response_fields as query_observed_fields,
)
from .response_queries import get_response_field_schema as query_response_schema
from .response_queries import list_response_fields as query_response_fields
from .traversal import (
    _DEFAULT_LIST_LIMIT,
    _closest_operation_keys,
)


class OpenAPIToolBackend:
    """Answer OpenAPI Tools from current trusted App context and observations.

    Args:
        context_provider: Returns the initialized target and parsed OpenAPI IR.
        observed_response_reader: Optionally reads retained response evidence
            from API Behavior Monitor through bounded Catalog pages.
    """

    def __init__(
        self,
        *,
        context_provider: Callable[[], ToolContext],
        observed_response_reader: ObservedResponseReader | None = None,
    ) -> None:
        """Retain callbacks without reading target state during composition."""
        self._context_provider = context_provider
        self._observed_response_reader = observed_response_reader

    def list_inputs(
        self,
        *,
        operation_key: str,
        media_type: str | None = None,
        prefix: str | None = None,
        offset: int = 0,
        limit: int = _DEFAULT_LIST_LIMIT,
    ) -> dict[str, object]:
        """Return one deterministic page of semantic request-input handles."""
        return query_inputs(
            operation_resolver=self._operation,
            operation_key=operation_key,
            media_type=media_type,
            prefix=prefix,
            offset=offset,
            limit=limit,
        )

    def list_operations(
        self,
        *,
        offset: int = 0,
        limit: int = _DEFAULT_LIST_LIMIT,
    ) -> dict[str, object]:
        """Return one stable page of exact operation identities."""
        ir = self._current_ir()
        operations = [ir.operations[key] for key in sorted(ir.operations)]
        page = operations[offset : offset + limit]
        result: dict[str, object] = {
            "operations": [
                {
                    "operation_key": item.operation_key,
                    "method": item.method.upper(),
                    "path": item.path,
                    "deprecated": bool(getattr(item, "deprecated", False)),
                }
                for item in page
            ],
            "total": len(operations),
            "offset": offset,
        }
        if offset + len(page) < len(operations):
            result["next_offset"] = offset + len(page)
        return {"structured": result}

    def list_response_fields(
        self,
        *,
        operation_key: str,
        status_code: int | str,
        offset: int = 0,
        limit: int = _DEFAULT_LIST_LIMIT,
    ) -> dict[str, object]:
        """Return one deterministic page of declared response-field handles."""
        return query_response_fields(
            operation_resolver=self._operation,
            operation_key=operation_key,
            status_code=status_code,
            offset=offset,
            limit=limit,
        )

    def find_observed_response_fields(
        self,
        *,
        name: str,
        offset: int = 0,
        limit: int = _DEFAULT_LIST_LIMIT,
    ) -> dict[str, object]:
        """Return similarly named fields backed by retained scalar evidence."""
        return query_observed_fields(
            ir_provider=self._current_ir,
            observed_response_reader=self._observed_response_reader,
            name=name,
            offset=offset,
            limit=limit,
        )

    def get_input_schema(
        self,
        *,
        operation_key: str,
        input: str,
        media_type: str | None = None,
    ) -> dict[str, object]:
        """Return the compact Schema for one exact request-input handle."""
        return query_input_schema(
            operation_resolver=self._operation,
            operation_key=operation_key,
            input=input,
            media_type=media_type,
        )

    def get_response_field_schema(
        self,
        *,
        operation_key: str,
        status_code: int | str,
        field: str,
        media_type: str | None = None,
    ) -> dict[str, object]:
        """Return the compact Schema for one exact response-field handle."""
        return query_response_schema(
            operation_resolver=self._operation,
            operation_key=operation_key,
            status_code=status_code,
            field=field,
            media_type=media_type,
        )

    def _operation(self, operation_key: str) -> OperationIR:
        """Resolve one exact operation or return bounded recovery choices."""
        ir = self._current_ir()
        try:
            return ir.operations[operation_key]
        except KeyError as exc:
            candidates = _closest_operation_keys(operation_key, ir.operations)
            recovery = (
                ". Closest existing operation keys: " + ", ".join(candidates)
                if candidates
                else ""
            )
            raise ToolFailure(
                code="openapi_operation_not_found",
                message=f"OpenAPI operation was not found: {operation_key}{recovery}",
            ) from exc

    def _current_ir(self) -> OpenAPISpecIR:
        """Return the parsed OpenAPI IR from initialized trusted context."""
        return self._context_provider().ir
