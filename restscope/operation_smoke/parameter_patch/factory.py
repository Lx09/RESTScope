"""Fresh Parameter Patch Agent construction per Solve requirement."""

from __future__ import annotations

from restscope.llm import LLMClient, LLMModelConfig
from restscope.observability import TracingRuntime

from .agent import ParameterPatchAgent


class ParameterPatchAgentFactory:
    """Create isolated Patch Agents sharing only immutable dependencies."""

    def __init__(
        self,
        *,
        client: LLMClient,
        model: LLMModelConfig,
        tracing_runtime: TracingRuntime | None = None,
    ) -> None:
        """Store immutable collaborators reused by otherwise isolated Agents."""
        self.client = client
        self.model = model
        self.tracing_runtime = tracing_runtime or TracingRuntime.disabled()

    def create(self) -> ParameterPatchAgent:
        """Return a fresh Agent with no proposal or sample conversation."""
        return ParameterPatchAgent(
            client=self.client,
            model=self.model,
            tracing_runtime=self.tracing_runtime,
        )
