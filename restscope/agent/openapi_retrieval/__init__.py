"""IR-backed OpenAPI investigation Agent package."""

from .agent import OpenAPIRetrievalAgent, OpenAPIRetrievalOutputError
from .factory import build_openapi_retrieval_agent
from .investigation import OpenAPIRetrievalQueryError
from .schemas import (
    EvidenceConflict,
    OpenAPIRetrievalRequest,
    OpenAPIRetrievalResult,
    InvestigationAction,
    InvestigationSummary,
    ParameterProducerCandidate,
    ParameterValueProducerQuery,
    RetrievalEvidence,
    TargetParameterMatch,
    TargetParameterSummary,
)
from .tool import OPENAPI_RETRIEVAL_TOOL_NAME, register_openapi_retrieval_tool

__all__ = [
    "EvidenceConflict",
    "OpenAPIRetrievalAgent",
    "OpenAPIRetrievalOutputError",
    "OpenAPIRetrievalRequest",
    "OpenAPIRetrievalResult",
    "OpenAPIRetrievalQueryError",
    "OPENAPI_RETRIEVAL_TOOL_NAME",
    "InvestigationAction",
    "InvestigationSummary",
    "ParameterProducerCandidate",
    "ParameterValueProducerQuery",
    "RetrievalEvidence",
    "TargetParameterMatch",
    "TargetParameterSummary",
    "build_openapi_retrieval_agent",
    "register_openapi_retrieval_tool",
]
