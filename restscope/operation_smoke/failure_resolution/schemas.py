"""Define the reference-only state exchanged with Failure Resolution Agent.

The Agent may rewrite semantic groupings and progress at any time, but it can
name precise runtime evidence only through short references issued by the
current Resolution session. Patch candidates, Test Cases, database records,
and executable Generator or Constraint objects never enter these DTOs.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from restscope.tools.worklist import FailureWorklist, WorklistDecision, WorklistItem
from restscope.tools.worklist.contracts import WORKLIST_ITEM_ID_PATTERN


class _Model(BaseModel):
    """Reject embedded runtime objects and other undeclared model output."""

    model_config = ConfigDict(extra="forbid")


class FailureSource(_Model):
    """Name one exact Failure message and all Batch cases that produced it.

    The registry keeps the complete normalized message because final stable
    Failure identity must not depend on prompt truncation. The Context adapter,
    not this authoritative DTO, bounds the model-facing projection.
    """

    failure_ref: str = Field(pattern=r"^E[1-9][0-9]*$")
    message: str = Field(min_length=1)
    test_case_refs: list[str] = Field(min_length=1, max_length=1_100)

    @model_validator(mode="after")
    def validate_case_refs(self) -> "FailureSource":
        """Keep each exact source-to-case association unique."""
        if len(self.test_case_refs) != len(set(self.test_case_refs)):
            raise ValueError("Failure source Test Case references must be unique")
        if any(
            not _is_reference(value, prefix="TC")
            for value in self.test_case_refs
        ):
            raise ValueError("Failure source contains an invalid Test Case reference")
        return self


class FailureResolutionFinish(_Model):
    """Let the Agent request final validation of its current worklist."""

    reason: str = Field(min_length=1, max_length=1_200)


class FailureResolutionRequest(_Model):
    """Identify one failed Batch while keeping its Test Cases in the Catalog."""

    operation_key: str = Field(min_length=1, max_length=1_000)
    round_number: int = Field(ge=1)
    batch_run_id: str = Field(min_length=1, max_length=200)
    case_ids: list[str] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def validate_case_ids(self) -> "FailureResolutionRequest":
        """Require unique run-local TC references for deterministic source folding."""
        if len(self.case_ids) != len(set(self.case_ids)):
            raise ValueError("Failure Resolution case_ids must be unique")
        if any(not _is_reference(value, prefix="TC") for value in self.case_ids):
            raise ValueError("Failure Resolution contains an invalid Test Case reference")
        return self


class ResolutionCommit(_Model):
    """Summarize one successful atomic finalization without precise Patch DTOs."""

    items: list["ResolutionItemCommit"] = Field(default_factory=list, max_length=100)
    attempt_ids: list[str] = Field(default_factory=list, max_length=100)
    generator_change_event_ids: list[str] = Field(default_factory=list, max_length=100)
    applied_candidate_refs: list[str] = Field(default_factory=list, max_length=100)


class ResolutionItemCommit(_Model):
    """Return trusted finalization facts for one decided worklist item.

    ``failure_summary`` is generated from registry-owned Failure messages, not
    copied from Agent-authored worklist text.
    """

    item_id: str = Field(
        min_length=6,
        max_length=120,
        pattern=WORKLIST_ITEM_ID_PATTERN,
    )
    failure_summary: str = Field(min_length=1, max_length=1_200)
    outcome: Literal["apply_patch", "no_patch"]
    failure_id: str = Field(min_length=1)
    attempt_id: str = Field(min_length=1)
    candidate_ref: str | None = Field(default=None, pattern=r"^P[1-9][0-9]*$")
    generator_change_event_id: str | None = None
    patch_outputs: int | None = Field(default=None, ge=1, le=1_000)
    changed_input_count: int | None = Field(default=None, ge=0)
    constraint_count: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_patch_identity(self) -> "ResolutionItemCommit":
        """Require Patch and event references exactly for apply-Patch decisions."""
        patch_fields = (
            self.candidate_ref is not None,
            self.generator_change_event_id is not None,
            self.patch_outputs is not None,
            self.changed_input_count is not None,
            self.constraint_count is not None,
        )
        if self.outcome == "apply_patch" and not all(patch_fields):
            raise ValueError("apply_patch commit requires candidate and event refs")
        if self.outcome == "no_patch" and any(patch_fields):
            raise ValueError("no_patch commit cannot include Patch identities")
        return self


class FailureResolutionOutcome(_Model):
    """Return a completed commit or the single Operation-wide hard-stop result."""

    status: Literal["completed", "failure_resolution_limit_exceeded"]
    outputs_used: int = Field(ge=0, le=1_000)
    source_count: int = Field(ge=1, le=100)
    worklist: FailureWorklist
    commit: ResolutionCommit | None = None
    reason: str | None = Field(default=None, min_length=1, max_length=1_200)

    @model_validator(mode="after")
    def validate_terminal_state(self) -> "FailureResolutionOutcome":
        """Keep committed completion separate from output-limit exhaustion."""
        if self.status == "completed":
            if self.commit is None or self.reason is None:
                raise ValueError("completed Resolution requires commit and reason")
        elif self.commit is not None:
            raise ValueError("output-limit exhaustion cannot include a commit")
        return self


def _is_reference(value: str, *, prefix: str) -> bool:
    """Recognize one positive decimal short reference without regex duplication."""
    if not value.startswith(prefix):
        return False
    number = value.removeprefix(prefix)
    return number.isdigit() and int(number) > 0
