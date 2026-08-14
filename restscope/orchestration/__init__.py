"""Expose the one public entry point for RESTScope's long-task loop.

The App supplies a Harness-compatible System Agent runner and an optional run
focus. :class:`OrchestrationRuntime` returns the immutable Goal and final
Ledger snapshot; planning details remain owned by this package.
"""

from .models import OrchestrationResult
from .runtime import OrchestrationRuntime

__all__ = ["OrchestrationResult", "OrchestrationRuntime"]
