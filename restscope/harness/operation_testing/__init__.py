"""Execute generated request Batches and return bounded inline outcomes.

Request generation produces complete requests before this package performs
network effects. The service executes each request in stable order without a
Test Case Catalog, Worklist, Failure identity, or persistence record.
"""

from .service import BatchExecutionResult, OperationTestingService, TestingExecutionError
from .outcomes import (
    BatchCaseOutcome,
    HTTPFailure,
    TransportFailure,
)

__all__ = [
    "BatchExecutionResult",
    "BatchCaseOutcome",
    "HTTPFailure",
    "OperationTestingService",
    "TestingExecutionError",
    "TransportFailure",
]
