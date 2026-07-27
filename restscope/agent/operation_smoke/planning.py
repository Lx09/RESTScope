"""Typed decisions for one active Operation Smoke failure."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Literal

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


@dataclass(slots=True, frozen=True)
class FailureDecisionProtocol:
    """DTO-derived model-facing contract for one failure decision."""

    allowed_fields: tuple[str, ...]
    examples: dict[str, dict[str, Any]]
    text: str


def build_failure_decision_protocol(
    *,
    input_handle: str | None,
    failure_ref: str,
    observation_ref: str | None = None,
) -> FailureDecisionProtocol:
    """Render compact action examples that are valid FailureDecision values."""

    examples: dict[str, dict[str, Any]] = {}
    if input_handle is not None:
        ready = {
            "action": "ready",
            "cause": "Existing evidence identifies the parameter cause.",
            "solutions": [
                {
                    "input": input_handle,
                    "desired_behavior": (
                        "Generate values accepted by this operation."
                    ),
                }
            ],
            "evidence_refs": [failure_ref],
            "interaction_notes": [],
        }
        examples["ready"] = ready
        examples["hypothesis"] = {
            "action": "hypothesis",
            "hypothesis": "One testable parameter explanation.",
            "target_inputs": [input_handle],
            "proposed_changes": [
                "Change the generated value while testing this explanation."
            ],
            "expected_outcome": (
                "The probe response changes as predicted by this explanation."
            ),
            "evidence_refs": [failure_ref],
        }
        if observation_ref is not None:
            examples["confirmed"] = {
                **ready,
                "action": "confirmed",
                "evidence_refs": [failure_ref, observation_ref],
            }
    examples["deferred"] = {
        "action": "deferred",
        "reason": "No safe parameter diagnosis is supported by the evidence.",
    }

    for action, payload in examples.items():
        decision = FailureDecision.model_validate(payload)
        semantic_errors = decision.semantic_errors()
        if semantic_errors:
            raise RuntimeError(
                f"invalid {action} FailureDecision protocol example: "
                + "; ".join(semantic_errors)
            )

    allowed_fields = tuple(FailureDecision.model_fields)
    lines = [
        "FailureDecision JSON protocol:",
        "Allowed top-level keys (exactly): "
        + ", ".join(allowed_fields)
        + ".",
        "Return no other top-level keys and return only fields relevant to "
        "the selected action.",
        "Never return failure_ref, explanation, reasoning, or "
        "desired_parameter_behavior.",
        "proposed_changes must be a JSON array of strings.",
        "solutions must be a JSON array of objects with input and "
        "desired_behavior; candidate_values and candidate_range are optional.",
        "Complete minimal examples using names and references supplied in "
        "this task:",
        json.dumps(
            examples,
            ensure_ascii=False,
            separators=(",", ":"),
        ),
    ]
    if input_handle is None:
        lines.append(
            "action=ready, action=hypothesis, and action=confirmed are "
            "unavailable because this operation has no configurable input."
        )
    elif observation_ref is None:
        lines.append(
            "action=confirmed is unavailable until Active hypothesis is "
            "non-null and its HTTP Observation is present in Evidence."
        )
    return FailureDecisionProtocol(
        allowed_fields=allowed_fields,
        examples=examples,
        text="\n".join(lines),
    )
