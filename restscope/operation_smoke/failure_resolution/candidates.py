"""Keep precise Patch candidates behind session-local opaque references.

Parameter Patch supplies reviewed executable objects to this registry. Failure
Resolution Agent sees only a short ``P*`` reference and a bounded semantic
summary; finalization is the only runtime path that retrieves the exact Patch,
sample evidence, and Parameter attribution again.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from restscope.capabilities import AgentToolbox, ToolFailure
from restscope.context import CompactTextWriter
from restscope.llm import ToolSpec
from restscope.operation_smoke.memory import SolveAttemptParameterWrite
from restscope.operation_smoke.parameter_patch import GeneratorPatchDraft


READ_CANDIDATE_TOOL_NAME = "parameter_patch.read_candidate"


class PatchCandidate(BaseModel):
    """Retain one complete reviewed Patch for trusted finalization only.

    ``affected_parameters`` uses the semantic handles shown to the Agent, while
    ``parameter_attributions`` retains the exact input-node identities required
    by persistence. The before/after summaries and samples are authoritative
    validation evidence, not values the Agent may rewrite.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    candidate_ref: str = Field(pattern=r"^P[1-9][0-9]*$")
    patch: GeneratorPatchDraft
    root_cause: str = Field(min_length=1, max_length=1_200)
    change_reason: str = Field(min_length=1, max_length=1_200)
    affected_parameters: list[str] = Field(min_length=1, max_length=100)
    parameter_attributions: list[SolveAttemptParameterWrite] = Field(
        min_length=1,
        max_length=100,
    )
    before_generators: dict[str, Any]
    after_generators: dict[str, Any]
    samples: list[dict[str, Any]] = Field(min_length=1, max_length=20)
    outputs_used: int = Field(ge=1, le=1_000)


class CandidateSampleOverview(BaseModel):
    """Summarize sample coverage without returning generated sample values."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    sample_count: int = Field(ge=1, le=20)
    covered_parameters: list[str] = Field(max_length=100)


class PatchCandidateSummary(BaseModel):
    """Expose only the bounded facts needed to recover Agent context."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    candidate_ref: str = Field(pattern=r"^P[1-9][0-9]*$")
    validation_status: Literal["validated"] = "validated"
    root_cause: str = Field(min_length=1, max_length=1_200)
    affected_parameters: list[str] = Field(min_length=1, max_length=100)
    generator_change_overview: list[str] = Field(max_length=100)
    constraint_change_overview: list[str] = Field(max_length=20)
    sample_overview: CandidateSampleOverview
    model_outputs_used: int = Field(ge=1, le=1_000)


class _ReadCandidateInput(BaseModel):
    """Accept exactly one candidate reference and no executable fields."""

    model_config = ConfigDict(extra="forbid")

    candidate_ref: str = Field(pattern=r"^P[1-9][0-9]*$")


class PatchCandidateRegistry:
    """Issue immutable P references and exclusively own their precise objects."""

    def __init__(self) -> None:
        """Create one empty registry whose lifetime is the Resolution session."""
        self._candidates: dict[str, PatchCandidate] = {}

    def issue(
        self,
        *,
        patch: GeneratorPatchDraft,
        root_cause: str,
        change_reason: str,
        affected_parameters: list[str],
        parameter_attributions: list[SolveAttemptParameterWrite],
        before_generators: dict[str, Any],
        after_generators: dict[str, Any],
        samples: list[dict[str, Any]],
        outputs_used: int,
    ) -> PatchCandidate:
        """Store a harness-built candidate and return its newly issued P ref."""
        candidate_ref = f"P{len(self._candidates) + 1}"
        candidate = PatchCandidate(
            candidate_ref=candidate_ref,
            patch=patch,
            root_cause=root_cause,
            change_reason=change_reason,
            affected_parameters=affected_parameters,
            parameter_attributions=parameter_attributions,
            before_generators=before_generators,
            after_generators=after_generators,
            samples=samples,
            outputs_used=outputs_used,
        )
        self._candidates[candidate_ref] = candidate.model_copy(deep=True)
        return candidate.model_copy(deep=True)

    def refs(self) -> frozenset[str]:
        """Return the currently valid P references for worklist validation."""
        return frozenset(self._candidates)

    def get(self, candidate_ref: str) -> PatchCandidate:
        """Retrieve a precise defensive copy for trusted runtime code only."""
        candidate = self._candidates.get(candidate_ref)
        if candidate is None:
            raise ToolFailure(
                code="unknown_patch_candidate",
                message=f"Unknown or expired Patch candidate: {candidate_ref}",
            )
        return candidate.model_copy(deep=True)

    def summary(self, candidate_ref: str) -> PatchCandidateSummary:
        """Project one precise candidate into a non-executable model view."""
        candidate = self.get(candidate_ref)
        changed_parameters = [
            handle
            for handle in candidate.affected_parameters
            if candidate.before_generators.get(handle)
            != candidate.after_generators.get(handle)
        ]
        covered_parameters = sorted(
            {
                handle
                for sample in candidate.samples
                for handle in _sample_handles(sample)
                if handle in candidate.affected_parameters
            }
        )
        return PatchCandidateSummary(
            candidate_ref=candidate.candidate_ref,
            root_cause=candidate.root_cause,
            affected_parameters=candidate.affected_parameters,
            generator_change_overview=[
                f"{handle}: generator changed" for handle in changed_parameters
            ],
            constraint_change_overview=[
                f"{constraint.kind}: relationship replacement"
                for constraint in candidate.patch.constraints
            ],
            sample_overview=CandidateSampleOverview(
                sample_count=len(candidate.samples),
                covered_parameters=covered_parameters,
            ),
            model_outputs_used=candidate.outputs_used,
        )


def read_candidate_tool_spec() -> ToolSpec:
    """Describe bounded recovery of one issued candidate's semantic details."""
    return ToolSpec(
        name=READ_CANDIDATE_TOOL_NAME,
        description=(
            "Read a validated Patch candidate summary by P* reference. The "
            "result never includes executable Patch DTOs or generated values."
        ),
        kind="local_function",
        input_schema=_ReadCandidateInput.model_json_schema(),
        output_schema=PatchCandidateSummary.model_json_schema(),
        strict=True,
    )


def register_candidate_read_tool(
    *,
    toolbox: AgentToolbox,
    registry: PatchCandidateRegistry,
) -> None:
    """Register the read-only candidate projection on an Agent toolbox."""

    def read(candidate_ref: str) -> dict[str, Any]:
        """Return both compact Markdown and the strict summary DTO."""
        summary = registry.summary(candidate_ref)
        writer = CompactTextWriter(max_value_chars=1_200)
        writer.section(
            f"VALIDATED PATCH CANDIDATE {summary.candidate_ref}",
            untrusted=True,
        )
        writer.record(
            "candidate",
            root_cause=summary.root_cause,
            affected_parameters=summary.affected_parameters,
            generator_changes=summary.generator_change_overview,
            constraint_changes=summary.constraint_change_overview,
            sample_count=summary.sample_overview.sample_count,
            sample_coverage=summary.sample_overview.covered_parameters,
        )
        return {
            "content": writer.render(max_chars=8_000).text,
            "structured": summary.model_dump(mode="json"),
        }

    toolbox.register(spec=read_candidate_tool_spec(), execute=read)


def _sample_handles(sample: dict[str, Any]) -> set[str]:
    """Find semantic handles in sample metadata without exposing their values."""
    found: set[str] = set()
    for field in ("values", "present"):
        value = sample.get(field)
        if isinstance(value, dict):
            found.update(key for key in value if isinstance(key, str))
    return found
