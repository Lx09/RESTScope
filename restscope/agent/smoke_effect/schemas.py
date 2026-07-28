"""Typed inputs and outputs for candidate effect validation.

Effect receives expanded before-and-after evidence, the Solve requirement, and
the executable Patch. It makes one semantic decision for the whole candidate;
the coordinator never accepts only a subset of that Patch.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from restscope.agent.failure_solver import PatchRequirement
from restscope.agent.smoke_plan import FailureTodo


class _Model(BaseModel):
    """Reject extra fields at the independent Effect boundary."""

    model_config = ConfigDict(extra="forbid")


class SmokeEffectRequest(_Model):
    """Complete evidence for one atomic candidate comparison."""

    operation_key: str = Field(min_length=1)
    todo: FailureTodo
    patch_requirement: PatchRequirement
    patch: dict[str, Any]
    before_batch: dict[str, Any]
    candidate_batch: dict[str, Any]
    history: list[dict[str, Any]] = Field(default_factory=list)


class SmokeEffectDecision(_Model):
    """One valid semantic assessment of the candidate."""

    outcome: Literal[
        "resolved_without_regression",
        "unresolved",
        "regression",
        "unknown",
    ]
    reason: str = Field(min_length=1)


class SmokeEffectOutcome(SmokeEffectDecision):
    """Effect assessment plus the number of consumed model outputs."""

    outputs_used: int = Field(ge=1, le=2)
    output_history: list[dict[str, Any]] = Field(default_factory=list)
