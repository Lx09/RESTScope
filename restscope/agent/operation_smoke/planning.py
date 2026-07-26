"""Typed PlanState transitions for multi-round Smoke diagnosis."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .evidence import EvidenceJournal
from .schemas import PendingPlanItemSummary, PlanItemSummary


class _DecisionModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ParameterSolutionDecision(_DecisionModel):
    input: str = Field(min_length=1, max_length=1000)
    desired_behavior: str = Field(min_length=1, max_length=4000)
    candidate_values: list[Any] = Field(default_factory=list, max_length=100)
    candidate_range: list[float | int] | None = Field(
        default=None,
        min_length=2,
        max_length=2,
    )


class ReadyPlanDecision(_DecisionModel):
    item_id: str | None = Field(default=None, min_length=1, max_length=20)
    failure_refs: list[str] = Field(min_length=1, max_length=100)
    cause: str = Field(min_length=1, max_length=4000)
    confidence: float = Field(ge=0, le=1)
    solutions: list[ParameterSolutionDecision] = Field(
        min_length=1,
        max_length=100,
    )
    evidence_refs: list[str] = Field(default_factory=list, max_length=100)
    interaction_notes: list[str] = Field(default_factory=list, max_length=100)


class PendingPlanDecision(_DecisionModel):
    item_id: str | None = Field(default=None, min_length=1, max_length=20)
    failure_refs: list[str] = Field(min_length=1, max_length=100)
    hypothesis: str = Field(min_length=1, max_length=4000)
    missing_evidence: str = Field(min_length=1, max_length=4000)
    next_probe: str = Field(min_length=1, max_length=4000)
    evidence_refs: list[str] = Field(default_factory=list, max_length=100)


class PlanDecision(_DecisionModel):
    ready: list[ReadyPlanDecision] = Field(default_factory=list, max_length=100)
    pending: list[PendingPlanDecision] = Field(
        default_factory=list,
        max_length=100,
    )
    non_parameter_failure_refs: list[str] = Field(
        default_factory=list,
        max_length=100,
    )
    unplanned_failure_refs: list[str] = Field(
        default_factory=list,
        max_length=100,
    )
    finish: bool = False


class ReadyPlanItem(ReadyPlanDecision):
    item_id: str = Field(min_length=1, max_length=20)

    def summary(self) -> PlanItemSummary:
        return PlanItemSummary(
            item_id=self.item_id,
            failure_refs=self.failure_refs,
            cause=self.cause,
            confidence=self.confidence,
            affected_inputs=[item.input for item in self.solutions],
            solution="; ".join(
                f"{item.input}: {item.desired_behavior}"
                for item in self.solutions
            ),
            evidence_refs=self.evidence_refs,
            interaction_notes=self.interaction_notes,
        )


class PendingPlanItem(PendingPlanDecision):
    item_id: str = Field(min_length=1, max_length=20)

    def summary(self) -> PendingPlanItemSummary:
        return PendingPlanItemSummary(
            item_id=self.item_id,
            failure_refs=self.failure_refs,
            hypothesis=self.hypothesis,
            missing_evidence=self.missing_evidence,
            next_probe=self.next_probe,
            evidence_refs=self.evidence_refs,
        )


class PlanState(BaseModel):
    """One validated full snapshot; no failure may silently disappear."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    ready: list[ReadyPlanItem] = Field(default_factory=list)
    pending: list[PendingPlanItem] = Field(default_factory=list)
    non_parameter_failure_refs: list[str] = Field(default_factory=list)
    unplanned_failure_refs: list[str] = Field(default_factory=list)
    finish: bool = False
    next_item_number: int = Field(default=1, ge=1)

    @classmethod
    def from_decision(
        cls,
        decision: PlanDecision,
        *,
        journal: EvidenceJournal,
        previous: "PlanState | None",
    ) -> tuple["PlanState | None", list[str]]:
        errors = _validate_decision(
            decision,
            journal=journal,
            previous=previous,
        )
        if errors:
            return None, errors
        next_number = previous.next_item_number if previous is not None else 1
        ready: list[ReadyPlanItem] = []
        pending: list[PendingPlanItem] = []
        used_ids: set[str] = set()
        for source, target in (
            (decision.ready, ready),
            (decision.pending, pending),
        ):
            for item in source:
                item_id = item.item_id
                if item_id is None:
                    while f"I{next_number}" in used_ids:
                        next_number += 1
                    item_id = f"I{next_number}"
                    next_number += 1
                used_ids.add(item_id)
                target.append(
                    (
                        ReadyPlanItem if isinstance(item, ReadyPlanDecision)
                        else PendingPlanItem
                    ).model_validate(
                        item.model_dump() | {"item_id": item_id}
                    )
                )
        return (
            cls(
                ready=ready,
                pending=pending,
                non_parameter_failure_refs=(
                    decision.non_parameter_failure_refs
                ),
                unplanned_failure_refs=decision.unplanned_failure_refs,
                finish=decision.finish,
                next_item_number=next_number,
            ),
            [],
        )

    def prompt_view(self) -> dict[str, Any]:
        return {
            "ready": [item.model_dump(mode="json") for item in self.ready],
            "pending": [
                item.model_dump(mode="json") for item in self.pending
            ],
            "non_parameter_failure_refs": self.non_parameter_failure_refs,
            "unplanned_failure_refs": self.unplanned_failure_refs,
        }


def _validate_decision(
    decision: PlanDecision,
    *,
    journal: EvidenceJournal,
    previous: PlanState | None,
) -> list[str]:
    errors: list[str] = []
    known_failures = journal.known_failure_refs
    known_evidence = journal.known_evidence_refs
    known_inputs = set(journal.semantic_inputs.node_by_handle)
    previous_ids = (
        {
            item.item_id
            for item in [*previous.ready, *previous.pending]
        }
        if previous is not None
        else set()
    )
    supplied_ids: list[str] = []
    classified: list[str] = []
    for item in [*decision.ready, *decision.pending]:
        if item.item_id is not None:
            supplied_ids.append(item.item_id)
            if item.item_id not in previous_ids:
                errors.append(
                    f"{item.item_id} was not supplied as a plan item."
                )
        classified.extend(item.failure_refs)
        for failure_ref in item.failure_refs:
            if failure_ref not in known_failures:
                errors.append(
                    f"{failure_ref} was not supplied as a failure."
                )
        for evidence_ref in item.evidence_refs:
            if evidence_ref not in known_evidence:
                errors.append(
                    f"{evidence_ref} was not supplied as evidence."
                )
        if isinstance(item, ReadyPlanDecision):
            for solution in item.solutions:
                if solution.input not in known_inputs:
                    errors.append(
                        f"{solution.input} was not offered as an input."
                    )
    duplicates = sorted(
        item_id
        for item_id in set(supplied_ids)
        if supplied_ids.count(item_id) > 1
    )
    if duplicates:
        errors.append(
            f"Plan item IDs cannot repeat: {', '.join(duplicates)}."
        )
    classified.extend(decision.non_parameter_failure_refs)
    classified.extend(decision.unplanned_failure_refs)
    for failure_ref in [
        *decision.non_parameter_failure_refs,
        *decision.unplanned_failure_refs,
    ]:
        if failure_ref not in known_failures:
            errors.append(f"{failure_ref} was not supplied as a failure.")
    for failure_ref in sorted(known_failures):
        if classified.count(failure_ref) != 1:
            errors.append(
                f"{failure_ref} must be classified exactly once."
            )
    return errors
