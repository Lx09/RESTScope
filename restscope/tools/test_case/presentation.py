"""Bound and serialize Test Case facts before they enter model context.

The run-local Catalog may hold large or deeply nested values. These helpers
retain useful evidence while clipping container sizes and text at the Tool
boundary.
"""

from __future__ import annotations

import json
from typing import Any

from restscope.llm import ToolResult

_MAX_TOOL_VALUE_CHARS = 1_200

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
    """Bound every selected fact while preserving the case-keyed envelope."""
    cases = result.get("cases")
    assert isinstance(cases, dict)
    return {
        "cases": {
            case_id: {
                name: _bound_tool_values(value)
                for name, value in facts.items()
            }
            for case_id, facts in cases.items()
        }
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
