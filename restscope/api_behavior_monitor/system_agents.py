"""Define the narrow System Agent runner seam used by behavior monitoring.

Resource tracking submits bounded selection evidence through
this Interface and receive only Harness-validated JSON output. The production
Adapter is ``HarnessRuntime``; tests use small scripted adapters. No model,
prompt session, Tool catalog, or lifecycle state crosses this seam.
"""

from __future__ import annotations

from typing import Protocol

from restscope.agent import SystemAgentResult, SystemAgentTask


RESOURCE_IDENTIFIER_PROFILE_NAME = "resource-identifier-selector"


class SystemAgentRunner(Protocol):
    """Run one registered Profile synchronously through the Agent Harness."""

    def run_system_agent(
        self,
        profile_name: str,
        task: SystemAgentTask,
    ) -> SystemAgentResult:
        """Return a validated decision or a bounded terminal runtime failure."""
        ...
