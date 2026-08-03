"""Create a fresh Failure Solve Agent for every fixed-round todo."""

from __future__ import annotations

from restscope.capabilities import OpenAPICapability
from restscope.llm import LLMClient, LLMModelConfig
from restscope.observability import TracingRuntime
from restscope.testing import ReferenceValueProvider

from .agent import (
    FailureSolveAgent,
    HTTPProbe,
    PatchCoordinatorFactory,
    PatchApplication,
    SolveMemory,
)


class FailureSolveAgentFactory:
    """Share immutable collaborators while isolating each todo's Agent."""

    def __init__(
        self,
        *,
        client: LLMClient,
        model: LLMModelConfig,
        http_probe: HTTPProbe,
        memory: SolveMemory,
        patch_coordinator_factory: PatchCoordinatorFactory,
        patch_application: PatchApplication,
        openapi_capability: OpenAPICapability,
        reference_values: ReferenceValueProvider | None = None,
        tracing_runtime: TracingRuntime | None = None,
    ) -> None:
        """Store stateless services shared by fresh Failure Solve sessions."""
        self.client = client
        self.model = model
        self.http_probe = http_probe
        self.memory = memory
        self.patch_coordinator_factory = patch_coordinator_factory
        self.patch_application = patch_application
        self.openapi_capability = openapi_capability
        self.reference_values = reference_values
        self.tracing_runtime = tracing_runtime or TracingRuntime.disabled()

    def create(self) -> FailureSolveAgent:
        """Return a fresh Agent with no conversation or observations."""
        return FailureSolveAgent(
            client=self.client,
            model=self.model,
            http_probe=self.http_probe,
            memory=self.memory,
            patch_coordinator_factory=self.patch_coordinator_factory,
            patch_application=self.patch_application,
            openapi_capability=self.openapi_capability,
            reference_values=self.reference_values,
            tracing_runtime=self.tracing_runtime,
        )
