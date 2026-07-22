"""Explicit capability registration for bound-IR OpenAPI investigation."""

from __future__ import annotations

from restscope.capabilities import ToolContext
from restscope.capabilities.tool_registry import ToolRegistry
from restscope.llm import ToolSpec

from .agent import OpenAPIRetrievalAgent
from .schemas import OpenAPIRetrievalRequest, OpenAPIRetrievalResult


OPENAPI_RETRIEVAL_TOOL_NAME = "restscope.openapi.retrieve"


def register_openapi_retrieval_tool(
    registry: ToolRegistry,
    agent: OpenAPIRetrievalAgent,
) -> ToolSpec:
    """Register the read-only Agent wrapper in an explicitly supplied registry."""

    spec = ToolSpec(
        name=OPENAPI_RETRIEVAL_TOOL_NAME,
        description=(
            "Investigate the App-bound OpenAPI IR for operations that may produce "
            "a requested parameter value."
        ),
        kind="local_function",
        input_schema=OpenAPIRetrievalRequest.model_json_schema(),
        output_schema=OpenAPIRetrievalResult.model_json_schema(),
        risk_level="medium",
        read_only=True,
        requires_approval=False,
        timeout_seconds=120,
        metadata={
            "agent": "openapi_retrieval",
            "objectives": ["parameter_value_producer"],
            "state_lifecycle": "request_scoped",
        },
    )

    def retrieve_handler(context: ToolContext, /, **arguments: object) -> dict[str, object]:
        # ``context`` is injected by ToolExecutor, not supplied by the model.
        # This is how the public tool reuses the App's already parsed IR without
        # exposing a schema path or allowing the caller to replace the target.
        request = OpenAPIRetrievalRequest.model_validate(arguments)
        result = agent.retrieve(request, ir=context.ir)
        candidate_names = [
            candidate.operation.operation_id
            or f"{candidate.operation.method} {candidate.operation.path}"
            for candidate in result.candidates
        ]
        content = (
            f"OpenAPI retrieval {result.status}; candidates: "
            + (", ".join(candidate_names) if candidate_names else "none")
        )
        return {
            "content": content,
            "structured": result.model_dump(mode="json"),
        }

    registry.register(spec=spec, handler=retrieve_handler)
    return spec
