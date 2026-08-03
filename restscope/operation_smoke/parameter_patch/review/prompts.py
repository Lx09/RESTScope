"""Render a fresh, bounded semantic review context for one Patch candidate."""

from __future__ import annotations

from dataclasses import dataclass

from restscope.context import CompactTextWriter, ContextMetrics

from .schemas import ParameterPatchReviewCandidate


REVIEW_SYSTEM_PROMPT = """
# Purpose
Independently decide whether one already compiled and locally sampled Parameter
Patch candidate satisfies the supplied Solve requirement and acceptance
criteria.

# Authority
Judge semantic alignment only. Deterministic runtime code has already decided
DTO shape, affected-input scope, Schema compatibility, reference validity,
Constraint validity, compilation, and local generation safety. Do not repeat
or override those technical decisions. Do not reject merely because a real API
Batch has not run; that effect is measured later.

# Protocol
Call `submit_parameter_patch_review` exactly once. `issues` is authoritative:
use an empty array when the candidate aligns, otherwise list only concrete
mismatches between the candidate facts and the requirement. Set `accepted` to
true for an empty list and false for a non-empty list. Never call another tool,
emit prose, diagnose a different root cause, or propose a replacement.
""".strip()


@dataclass(slots=True, frozen=True)
class ParameterPatchReviewPrompt:
    """Carry safe review text and prompt-shaping metrics into AgentContext."""

    system: str
    user: str
    metrics: ContextMetrics


def build_parameter_patch_review_prompt(
    candidate: ParameterPatchReviewCandidate,
    *,
    system_prompt: str | None = None,
) -> ParameterPatchReviewPrompt:
    """Project only normalized final candidate facts into bounded Markdown."""
    writer = CompactTextWriter(max_value_chars=800)
    writer.section("SOLVE REQUIREMENT", untrusted=True)
    writer.detail("requirement", candidate.requirement)
    writer.detail("affected_inputs", {"items": candidate.affected_inputs})
    writer.section("GENERATOR CHANGE", untrusted=True)
    writer.detail("before", candidate.before_generators)
    writer.detail("after", candidate.after_generators)
    writer.section("SEMANTIC CANDIDATE PATCH", untrusted=True)
    writer.detail("proposal", candidate.proposal)
    writer.section("REFERENCE PROVENANCE", untrusted=True)
    writer.detail("references", {"items": candidate.reference_provenance})
    writer.section("CONSTRAINTS", untrusted=True)
    writer.detail("active", {"items": candidate.active_constraints})
    writer.detail("candidate", {"items": candidate.candidate_constraints})
    writer.section("GENERATED SAMPLES", untrusted=True)
    writer.detail("samples", {"items": candidate.samples})
    rendered = writer.render(max_chars=24_000)
    return ParameterPatchReviewPrompt(
        system=system_prompt or REVIEW_SYSTEM_PROMPT,
        user=rendered.text,
        metrics=rendered.metrics,
    )
