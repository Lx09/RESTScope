"""Construct isolated generic Agents only for low-level runtime tests.

Production roots must use registered ``run_system_agent`` calls. These tests
exercise lower-level prompt, Tool, budget, and child behavior, so they enter the
private resolver directly without restoring a public taskless Main lifecycle.
"""

from __future__ import annotations

from restscope.agent import Agent, AgentCompletion
from restscope.harness import HarnessRuntime


def start_test_agent(runtime: HarnessRuntime, profile_name: str = "main") -> Agent:
    """Create one bounded root whose caller must supply an ``AgentTask``.

    Args:
        runtime: Test Harness containing an already validated Profile graph.
        profile_name: Exact Profile whose grants the scenario needs to inspect.

    Returns:
        A fresh generic Agent. It is intentionally unavailable to production
        application code and accepts only one bounded task.
    """
    resolver = runtime.agent_runtime
    if resolver is None:
        raise RuntimeError("Test Harness has no Agent runtime")
    return resolver._start_root(
        profile_name=profile_name,
        lifecycle="subagent",
        rollout_budget_weighted_tokens=resolver.definition.rollout_budget_weighted_tokens,
        output_model=AgentCompletion,
        output_schema=AgentCompletion.model_json_schema(),
        output_schema_name="AgentCompletion",
        validate_output=lambda _output: (),
    )
