"""Define the complete model-facing Failure Resolution Worklist contract.

The Worklist Tool owns these reference-only DTOs because their fields and
cross-field rules are the JSON Schema shown to the model. The temporary
Failure Resolution workflow may store and interpret the same DTOs, but it does
not define a private Tool protocol.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


WORKLIST_ITEM_ID_PATTERN = r"^WI-(?:00[1-9]|0[1-9][0-9]|[1-9][0-9]{2,})$"
_PATCH_CANDIDATE_REF_PATTERN = r"^P[1-9][0-9]*$"
_WORKLIST_DECISION_JSON_SCHEMA = {
    "oneOf": [
        {
            "properties": {
                "outcome": {"const": "apply_patch"},
                "selected_candidate_ref": {
                    "type": "string",
                    "pattern": _PATCH_CANDIDATE_REF_PATTERN,
                },
            },
            "required": ["selected_candidate_ref"],
        },
        {
            "properties": {
                "outcome": {"const": "no_patch"},
                "selected_candidate_ref": {"type": "null"},
            },
        },
    ]
}


class _WorklistModel(BaseModel):
    """Reject embedded runtime objects and undeclared model output."""

    model_config = ConfigDict(extra="forbid")


class WorklistDecision(_WorklistModel):
    """Store a terminal judgment while keeping executable Patch objects hidden."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra=_WORKLIST_DECISION_JSON_SCHEMA,
    )

    outcome: Literal["apply_patch", "no_patch"]
    selected_candidate_ref: str | None = Field(
        default=None,
        pattern=_PATCH_CANDIDATE_REF_PATTERN,
        description=(
            "Required for apply_patch and omitted or null for no_patch. The "
            "same P* reference must also appear in the item's candidate_refs."
        ),
    )
    reason: str = Field(min_length=1, max_length=1_200)

    @model_validator(mode="after")
    def validate_action_fields(self) -> "WorklistDecision":
        """Require an opaque candidate exactly for an apply-Patch decision."""

        if self.outcome == "apply_patch" and self.selected_candidate_ref is None:
            raise ValueError("apply_patch requires selected_candidate_ref")
        if self.outcome == "no_patch" and self.selected_candidate_ref is not None:
            raise ValueError("no_patch cannot select a Patch candidate")
        return self


class WorklistItem(_WorklistModel):
    """Carry one mutable diagnosis composed only from references and short notes."""

    item_id: str = Field(
        min_length=6,
        max_length=120,
        pattern=WORKLIST_ITEM_ID_PATTERN,
    )
    source_failure_refs: list[str] = Field(min_length=1, max_length=100)
    test_case_refs: list[str] = Field(min_length=1, max_length=1_100)
    suspected_parameters: list[str] = Field(default_factory=list, max_length=100)
    progress: str = Field(default="", max_length=1_200)
    root_cause: str | None = Field(default=None, min_length=1, max_length=1_200)
    candidate_refs: list[str] = Field(default_factory=list, max_length=100)
    decision: WorklistDecision | None = None

    @model_validator(mode="after")
    def validate_reference_lists(self) -> "WorklistItem":
        """Reject repeated, malformed, or inconsistent reference lists."""

        named_lists = {
            "Failure source": self.source_failure_refs,
            "Test Case": self.test_case_refs,
            "Parameter": self.suspected_parameters,
            "Patch candidate": self.candidate_refs,
        }
        for label, values in named_lists.items():
            if len(values) != len(set(values)):
                raise ValueError(f"{label} references must be unique within an item")
        if any(not _is_reference(value, prefix="E") for value in self.source_failure_refs):
            raise ValueError("Worklist item contains an invalid Failure source reference")
        if any(not _is_reference(value, prefix="TC") for value in self.test_case_refs):
            raise ValueError("Worklist item contains an invalid Test Case reference")
        if any(not _is_reference(value, prefix="P") for value in self.candidate_refs):
            raise ValueError("Worklist item contains an invalid Patch candidate reference")
        selected = self.decision.selected_candidate_ref if self.decision else None
        if selected is not None and selected not in self.candidate_refs:
            raise ValueError(
                "The selected Patch candidate must be listed on the worklist item"
            )
        return self


class FailureWorklist(_WorklistModel):
    """Return the complete revisioned Agent-owned list at one instant."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    revision: int = Field(ge=0)
    active_item_id: str | None = Field(
        default=None,
        min_length=6,
        max_length=120,
        pattern=WORKLIST_ITEM_ID_PATTERN,
    )
    items: list[WorklistItem] = Field(default_factory=list)


def _is_reference(value: str, *, prefix: str) -> bool:
    """Accept a prefix followed by a positive decimal session number."""

    suffix = value.removeprefix(prefix)
    return value.startswith(prefix) and suffix.isdigit() and int(suffix) >= 1
