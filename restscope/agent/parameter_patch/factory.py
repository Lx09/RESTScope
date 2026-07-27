"""Fresh Parameter Patch Agent construction per Patch Group."""

from __future__ import annotations

from restscope.llm import LLMClient, LLMModelConfig
from restscope.observability import TracingRuntime

from .agent import ParameterPatchAgent


class ParameterPatchAgentFactory:
    """Create isolated Agent instances sharing only immutable dependencies."""

    def __init__(
        self,
        *,
        client: LLMClient,
        model: LLMModelConfig,
        tracing_runtime: TracingRuntime | None = None,
    ) -> None:
        self.client = client
        self.model = model
        self.tracing_runtime = tracing_runtime or TracingRuntime.disabled()

    def create(self) -> ParameterPatchAgent:
        """
        Handle create as part of one isolated Generator and Constraint Patch Group.

        The class owns any required collaborators or state; arguments supply only the
        data needed for this call.
        """
        return ParameterPatchAgent(
            client=self.client,
            model=self.model,
            tracing_runtime=self.tracing_runtime,
        )
