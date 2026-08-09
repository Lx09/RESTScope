"""Execute generated request batches and retain their run-local Test Cases.

Request generation produces complete requests before this package performs
network effects. The service then executes each request in a stable order and
returns immutable Test Case evidence for Operation Smoke and Tool backends.
"""

from .service import BatchExecutionResult, OperationTestingService, TestingExecutionError
from .probe_evidence import record_probe_result
from .test_case_catalog import (
    CatalogTestCase,
    CatalogTestCaseDraft,
    HTTPFailure,
    TestCaseCatalog,
    TransportFailure,
)

__all__ = [
    "BatchExecutionResult",
    "CatalogTestCase",
    "CatalogTestCaseDraft",
    "HTTPFailure",
    "OperationTestingService",
    "TestCaseCatalog",
    "TestingExecutionError",
    "TransportFailure",
    "record_probe_result",
]
