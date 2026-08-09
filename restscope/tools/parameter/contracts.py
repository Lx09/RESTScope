"""Define bounded model-facing summaries for validated Parameter Patch candidates.

Executable Generator and Constraint objects remain in the temporary Resolution
registry. These DTOs are owned by the read and generation Tools because their
JSON Schema is the only candidate detail exposed to a model.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class CandidateSampleOverview(BaseModel):
    """Summarize sample coverage without returning generated values."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    sample_count: int = Field(ge=1, le=20)
    covered_parameters: list[str] = Field(max_length=100)


class PatchCandidateSummary(BaseModel):
    """Expose the bounded facts needed to recover Agent context."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    candidate_ref: str = Field(pattern=r"^P[1-9][0-9]*$")
    validation_status: Literal["validated"] = "validated"
    root_cause: str = Field(min_length=1, max_length=1_200)
    affected_parameters: list[str] = Field(min_length=1, max_length=100)
    generator_change_overview: list[str] = Field(max_length=100)
    constraint_change_overview: list[str] = Field(max_length=20)
    sample_overview: CandidateSampleOverview
    model_outputs_used: int = Field(ge=1, le=1_000)
