"""Build, validate, inspect, and atomically apply Parameter Patches.

This deep module owns the complete state-changing workflow for request
generation.  Tool adapters call the three public runtime operations; compiler,
projection, persistence staging, and digest details remain internal here.
"""

from .models import (
    SemanticParameterPatch,
    SemanticResponseValueGenerator,
    SemanticResponseValueSource,
)
from .errors import ParameterPatchValidationError
from .runtime import (
    AppliedParameterPatch,
    AppliedReferenceBinding,
    RequestGenerationPatchRuntime,
    ValidatedPatch,
)
from .projection import constraint_closure, semantic_state_payload, validation_payload

__all__ = [
    "AppliedParameterPatch",
    "AppliedReferenceBinding",
    "ParameterPatchValidationError",
    "RequestGenerationPatchRuntime",
    "SemanticParameterPatch",
    "SemanticResponseValueGenerator",
    "SemanticResponseValueSource",
    "ValidatedPatch",
    "constraint_closure",
    "semantic_state_payload",
    "validation_payload",
]
