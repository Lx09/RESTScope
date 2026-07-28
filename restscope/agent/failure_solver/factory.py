"""Create a fresh Failure Solve Agent for every fixed-round todo."""

from __future__ import annotations

from restscope.llm import LLMClient, LLMModelConfig

from .agent import FailureSolveAgent, HTTPProbe


class FailureSolveAgentFactory:
    """Share immutable collaborators while isolating each todo's Agent."""

    def __init__(
        self,
        *,
        client: LLMClient,
        model: LLMModelConfig,
        http_probe: HTTPProbe,
    ) -> None:
        """Store the model client and current-operation HTTP safety boundary."""
        self.client = client
        self.model = model
        self.http_probe = http_probe

    def create(self) -> FailureSolveAgent:
        """Return a fresh Agent with no conversation or observations."""
        return FailureSolveAgent(
            client=self.client,
            model=self.model,
            http_probe=self.http_probe,
        )
