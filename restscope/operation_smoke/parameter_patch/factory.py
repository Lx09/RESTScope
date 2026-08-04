"""Fresh Parameter Patch Coordinator construction per Solve requirement."""

from __future__ import annotations

from restscope.capabilities import OpenAPICapability, ResourceIdentifierCapability
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
        openapi_capability: OpenAPICapability | None = None,
        resource_capability: ResourceIdentifierCapability | None = None,
        tracing_runtime: TracingRuntime | None = None,
    ) -> None:
        """Store immutable collaborators reused by otherwise isolated Agents."""
        self.client = client
        self.patch_model = patch_model
        self.review_model = review_model
        self.openapi_capability = openapi_capability
        self.resource_capability = resource_capability
        self.tracing_runtime = tracing_runtime or TracingRuntime.disabled()

    def create(self) -> ParameterPatchCoordinator:
        """Return a fresh Coordinator with no proposal or Review context."""
        return ParameterPatchCoordinator(
            client=self.client,
            patch_model=self.patch_model,
            review_model=self.review_model,
            openapi_capability=self.openapi_capability,
            resource_capability=self.resource_capability,
            tracing_runtime=self.tracing_runtime,
        )
