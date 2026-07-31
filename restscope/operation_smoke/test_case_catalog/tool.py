"""Expose exact Test Case Catalog reads as one Agent-local tool.

The tool is offered only inside Dedup and Solve sessions. It is not registered
in the App-wide capability registry because the Catalog exists for one
Coordinator run. Arguments and results use the provider's native compact JSON
protocol; traces retain only action and case counts, never queried values or
response bodies.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import ValidationError

from restscope.llm import ToolCall, ToolResult, ToolSpec
from restscope.observability import TracingRuntime

from .catalog import TestCaseCatalog
from .schemas import CatalogQuery


CATALOG_QUERY_TOOL_NAME = "query_test_case_catalog"
_MAX_TOOL_VALUE_CHARS = 1_200


def catalog_query_tool_spec() -> ToolSpec:
    """Return the provider schema for the five approved exact query actions."""
    return ToolSpec(
        name=CATALOG_QUERY_TOOL_NAME,
        description=(
            "Query exact request values, failed-response fields, or parsed "
            "Failure messages for known TC case references. This tool cannot "
            "list all Parameters or return a complete Test Case."
        ),
        kind="local_function",
        input_schema=CatalogQuery.model_json_schema(),
        output_schema={"type": "object"},
        risk_level="low",
        read_only=True,
        requires_approval=False,
        metadata={"open_world": False},
    )


def execute_catalog_query(
    *,
    catalog: TestCaseCatalog,
    tool_call: ToolCall,
    tracing_runtime: TracingRuntime,
) -> ToolResult:
    """Validate and execute one local Catalog read as a structured ToolResult."""
    action = tool_call.arguments.get("action")
    case_ids = tool_call.arguments.get("case_ids")
    with tracing_runtime.span(
        CATALOG_QUERY_TOOL_NAME,
        kind="TOOL",
        input_value={
            "action": action,
            "case_count": len(case_ids) if isinstance(case_ids, list) else 0,
        },
        attributes={"tool.name": CATALOG_QUERY_TOOL_NAME},
    ) as span:
        try:
            query = CatalogQuery.model_validate(tool_call.arguments)
            structured = _bound_catalog_result(catalog.query(query))
            result = ToolResult(
                tool_call_id=tool_call.id,
                name=tool_call.name,
                status="succeeded",
                structured=structured,
                metadata={"risk_level": "low", "read_only": True},
            )
        except (ValidationError, KeyError, ValueError) as exc:
            result = ToolResult(
                tool_call_id=tool_call.id,
                name=tool_call.name,
                status="failed",
                error={
                    "code": "invalid_catalog_query",
                    "message": str(exc),
                },
            )
        span.set_output(
            {
                "status": result.status,
                "action": action,
                "case_count": len(case_ids) if isinstance(case_ids, list) else 0,
            }
        )
        if result.status == "failed":
            span.mark_error((result.error or {}).get("message", "failed"))
        return result


def tool_result_json(result: ToolResult) -> str:
    """Serialize a native structured tool response without Markdown wrapping."""
    return json.dumps(
        result.model_dump(mode="json", exclude_none=True),
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _bound_tool_values(value: Any) -> Any:
    """Clip one selected scalar or container while retaining its original size."""
    if isinstance(value, str) and len(value) > _MAX_TOOL_VALUE_CHARS:
        retained = _MAX_TOOL_VALUE_CHARS - 200
        head = retained // 2
        tail = retained - head
        return {
            "truncated": True,
            "type": "string",
            "original_chars": len(value),
            "value": value[:head] + "…" + value[-tail:],
        }
    if isinstance(value, bytes):
        if len(value) <= _MAX_TOOL_VALUE_CHARS:
            return {
                "type": "bytes",
                "hex": value.hex(),
                "length": len(value),
            }
        retained = (_MAX_TOOL_VALUE_CHARS - 200) // 2
        return {
            "truncated": True,
            "type": "bytes",
            "original_bytes": len(value),
            "head_hex": value[:retained].hex(),
            "tail_hex": value[-retained:].hex(),
        }
    if isinstance(value, dict):
        bounded = {
            str(name): _bound_tool_values(child)
            for name, child in value.items()
        }
        return _clip_container(bounded, kind="object")
    if isinstance(value, list):
        bounded = [_bound_tool_values(child) for child in value]
        return _clip_container(bounded, kind="array")
    return value


def _bound_catalog_result(result: dict[str, Any]) -> dict[str, Any]:
    """Preserve the query envelope while bounding each selected case fact."""
    cases = result.get("cases")
    assert isinstance(cases, dict)
    return {
        "action": result["action"],
        "cases": {
            case_id: {
                name: _bound_tool_values(value)
                for name, value in facts.items()
            }
            for case_id, facts in cases.items()
        },
    }


def _clip_container(value: Any, *, kind: str) -> Any:
    """Replace a large object/array value with a typed head/tail JSON preview."""
    rendered = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    if len(rendered) <= _MAX_TOOL_VALUE_CHARS:
        return value
    retained = _MAX_TOOL_VALUE_CHARS - 240
    head = retained // 2
    tail = retained - head
    return {
        "truncated": True,
        "type": kind,
        "original_chars": len(rendered),
        "head": rendered[:head],
        "tail": rendered[-tail:],
    }
