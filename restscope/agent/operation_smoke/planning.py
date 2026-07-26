"""Typed decisions for one active Operation Smoke failure."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .schemas import ParameterSolution


class FailureDecision(BaseModel):
    """One complete decision for the currently active failure only."""

    model_config = ConfigDict(extra="forbid")

    action: Literal["ready", "hypothesis", "confirmed", "deferred"]
    cause: str | None = Field(default=None, min_length=1, max_length=4000)
    solutions: list[ParameterSolution] = Field(
        default_factory=list,
        max_length=100,
    )
    hypothesis: str | None = Field(
        default=None,
        min_length=1,
        max_length=4000,
    )
    target_inputs: list[str] = Field(default_factory=list, max_length=100)
    proposed_changes: list[str] = Field(default_factory=list, max_length=100)
    expected_outcome: str | None = Field(
        default=None,
        min_length=1,
        max_length=4000,
    )
    evidence_refs: list[str] = Field(default_factory=list, max_length=100)
    interaction_notes: list[str] = Field(default_factory=list, max_length=100)
    reason: str | None = Field(default=None, min_length=1, max_length=4000)

    def semantic_errors(self) -> list[str]:
        """Validate fields whose requirements depend on the selected action."""

        if self.action in {"ready", "confirmed"}:
            errors = []
            if self.cause is None:
                errors.append(f"{self.action} requires cause")
            if not self.solutions:
                errors.append(f"{self.action} requires solutions")
            if not self.evidence_refs:
                errors.append(f"{self.action} requires evidence_refs")
            return errors
        if self.action == "hypothesis":
            errors = []
            if self.hypothesis is None:
                errors.append("hypothesis requires hypothesis")
            if not self.target_inputs:
                errors.append("hypothesis requires target_inputs")
            if not self.proposed_changes:
                errors.append("hypothesis requires proposed_changes")
            if self.expected_outcome is None:
                errors.append("hypothesis requires expected_outcome")
            if not self.evidence_refs:
                errors.append("hypothesis requires evidence_refs")
            return errors
        return [] if self.reason is not None else ["deferred requires reason"]
