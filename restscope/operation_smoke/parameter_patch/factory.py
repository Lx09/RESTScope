"""Fresh Parameter Patch Coordinator construction per Solve requirement."""

from __future__ import annotations

from restscope.llm import LLMClient, LLMModelConfig
from restscope.observability import TracingRuntime

from .coordinator import ParameterPatchCoordinator


class ParameterPatchCoordinatorFactory:
    """Create isolated Coordinators sharing immutable model dependencies."""

    def __init__(
        self,
        *,
        client: LLMClient,
        patch_model: LLMModelConfig,
        review_model: LLMModelConfig,
        tracing_runtime: TracingRuntime | None = None,
    ) -> None:
        """Store immutable collaborators reused by otherwise isolated Agents."""
        self.client = client
        self.patch_model = patch_model
        self.review_model = review_model
        self.tracing_runtime = tracing_runtime or TracingRuntime.disabled()

    def create(self) -> ParameterPatchCoordinator:
        """Return a fresh Coordinator with no proposal or Review context."""
        return ParameterPatchCoordinator(
            client=self.client,
            patch_model=self.patch_model,
            review_model=self.review_model,
            tracing_runtime=self.tracing_runtime,
        )
