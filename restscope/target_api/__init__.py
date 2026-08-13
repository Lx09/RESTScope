"""Expose the shared foundation for requests to the tested target API.

Tools and internal Batch execution prepare requests without network effects,
then use one Client for safe target I/O and optional response observation.
"""

from .client import TargetAPIClient
from .errors import TargetAPIError, TargetAPITimeout
from .observation import (
    BufferedTargetResponse,
    TargetResponseObservation,
    TargetResponseOperationContext,
    TargetResponseProcessor,
    TargetResponseProcessorResult,
    TargetResponseProcessorWarning,
    TargetTransportObservation,
)
from .request import (
    PreparedTargetRequest,
    prepare_target_request,
)

__all__ = [
    "BufferedTargetResponse",
    "PreparedTargetRequest",
    "TargetAPIClient",
    "TargetAPIError",
    "TargetAPITimeout",
    "TargetResponseObservation",
    "TargetResponseOperationContext",
    "TargetResponseProcessor",
    "TargetResponseProcessorResult",
    "TargetResponseProcessorWarning",
    "TargetTransportObservation",
    "prepare_target_request",
]
