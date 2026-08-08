"""HTTP Tools for target requests and operation-scoped probes."""

from __future__ import annotations

from importlib import import_module
from typing import Any

from .request import (
    HTTP_REQUEST_TOOL_NAME,
    HTTPRequestArguments,
    HTTPRequestTimeoutError,
    HTTPRequestToolError,
    TargetHTTPRequestTool,
    http_request_tool_spec,
)

__all__ = [
    "HTTP_REQUEST_TOOL_NAME",
    "HTTPRequestArguments",
    "HTTPRequestTimeoutError",
    "HTTPRequestToolError",
    "TargetHTTPRequestTool",
    "CurrentOperationHTTPProbe",
    "http_request_tool_spec",
]


def __getattr__(name: str) -> Any:
    """Load the Harness-dependent Probe only when a caller requests it."""
    if name != "CurrentOperationHTTPProbe":
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(".probe", __name__), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    """Show the stable HTTP Tool Interface."""
    return sorted(set(globals()) | set(__all__))
