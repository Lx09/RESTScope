"""Public internal seam for stable Failure and Solve Attempt memory."""

from .memory import SmokeMemory
from .ports import SmokeMemoryRepository, SmokeMemoryUnitOfWork
from .patch_application import (
    AppliedSmokePatch,
    PatchSolveAttempt,
    SmokePatchApplication,
    normalize_patch_constraints,
    replace_constraint_scope,
)
from .schemas import (
    FailureBatchWrite,
    FailureHistory,
    FailureWrite,
    GeneratorChangeMemory,
    ParameterHistory,
    RecordedFailure,
    RecordedFailures,
    SolveAttemptMemory,
    SolveAttemptParameterWrite,
    SolveAttemptWrite,
)

__all__ = [
    "AppliedSmokePatch",
    "FailureBatchWrite",
    "FailureHistory",
    "FailureWrite",
    "GeneratorChangeMemory",
    "ParameterHistory",
    "PatchSolveAttempt",
    "RecordedFailure",
    "RecordedFailures",
    "SmokeMemory",
    "SmokeMemoryRepository",
    "SmokeMemoryUnitOfWork",
    "SmokePatchApplication",
    "SolveAttemptMemory",
    "SolveAttemptParameterWrite",
    "SolveAttemptWrite",
    "normalize_patch_constraints",
    "replace_constraint_scope",
]
