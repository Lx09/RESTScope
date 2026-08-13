"""Execute generated request Batches and return bounded inline outcomes.

Request generation produces complete requests before this package performs
network effects. The service executes each request in stable order without a
Test Case Catalog, Worklist, Failure identity, or persistence record.
"""

from .outcomes import (
    BatchCaseOutcome,
    HTTPFailure,
    TransportFailure,
)
from .service import (
    BatchExecutionResult,
    OperationTestingService,
    TestingExecutionError,
)

__all__ = [
    "BatchCaseOutcome",
    "BatchExecutionResult",
    "HTTPFailure",
    "OperationTestingService",
    "TestingExecutionError",
    "TransportFailure",
]
