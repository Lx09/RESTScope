"""Expose the target API request, transport, and response-observation boundary.

Callers prepare and send requests through this package instead of coupling URL
validation, network I/O, and API Behavior Monitor types in one root module.
"""

from .errors import TargetHTTPTimeout, TargetHTTPTransportError
from .observation import (
    BufferedTargetResponse,
    TargetOperationIdentity,
    TargetResponseObservation,
    TargetResponseOperationContext,
    TargetResponseProcessor,
    TargetResponseProcessorResult,
    TargetResponseProcessorWarning,
    current_target_operation_identity,
    target_operation_scope,
)
from .request import (
    PreparedTargetRequest,
    QueryItem,
    build_target_url,
    is_sensitive_header,
    merge_target_headers,
    validate_relative_target_path,
)
from .transport import ClientFactory, TargetHTTPTransport

__all__ = [
    "BufferedTargetResponse",
    "ClientFactory",
    "PreparedTargetRequest",
    "QueryItem",
    "TargetHTTPTimeout",
    "TargetHTTPTransport",
    "TargetHTTPTransportError",
    "TargetOperationIdentity",
    "TargetResponseObservation",
    "TargetResponseOperationContext",
    "TargetResponseProcessor",
    "TargetResponseProcessorResult",
    "TargetResponseProcessorWarning",
    "build_target_url",
    "current_target_operation_identity",
    "is_sensitive_header",
    "merge_target_headers",
    "target_operation_scope",
    "validate_relative_target_path",
]
