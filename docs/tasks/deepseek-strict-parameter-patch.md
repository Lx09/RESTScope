# Independent Strict Parameter Patch Review

Status: Implemented and verified; uncommitted

## Objective

Make DeepSeek transport reliable without asking the Patch proposal model to
judge its own locally generated samples. Split proposal and semantic review
into independent Agents behind one deterministic Coordinator.

## Approved scope

- Keep provider-neutral `ToolSpec.strict` and route all-strict official
  DeepSeek requests through `/beta`.
- Replace `submit_parameter_patch_decision` with the fixed-root,
  proposal-only `submit_parameter_patch_proposal` tool.
- Add a fresh-context `ParameterPatchReviewAgent` using the fixed-root
  `submit_parameter_patch_review` tool.
- Let `ParameterPatchCoordinator` own compilation, sampling, feedback,
  repeated-invalid protection, and the shared 20-output budget.
- Replace `ParameterPatchAgentFactory` with
  `ParameterPatchCoordinatorFactory`; keep Failure Solve's `P*`, result,
  persistence, Generator, Constraint, and real Batch Interfaces unchanged.
- Keep one independent strict-to-legacy fallback per Agent. Do not contact
  GitLab, a target API, or Phoenix.

## Non-goals

- Do not change Generator, Constraint, Memory, database, or Batch semantics.
- Do not persist Agent conversations, Reviewer decisions, or samples.
- Do not make the Reviewer re-run deterministic technical validation.
- Do not perform the separately authorized live DeepSeek schema request during
  ordinary local verification.
- Do not commit, merge, remove the worktree/branch, or push without separate
  authorization.

## Decisions

- The Patch Agent can only submit `{"action":"propose","patch":...}`.
  Model-side `action="accept"` and `ParameterPatchDecision` are deleted.
- The Reviewer submits `{"accepted":bool,"issues":[]}`. `issues` is the sole
  control value: local code normalizes acceptance to `not issues`; the raw
  boolean exists only in short-lived diagnostics.
- Every compiled candidate gets a new Reviewer and AgentContext. It receives
  normalized requirement, affected inputs, before/after Generators, semantic
  Patch, reference provenance, active/candidate Constraints, and samples. It
  never receives Patch dialogue, earlier errors, or model reasoning.
- Reviewer protocol correction stays with the same Reviewer and candidate.
  Compiler and semantic Review issues return to the matching proposal tool
  call in the original Patch Agent session.
- Provider failures that return no model response do not spend the shared
  budget. Proposal and successful legacy outputs do. A normal success spends
  two outputs.

## Verification

- Focused Patch, Review, Failure Solve, Coordinator, evaluation, Context,
  package-boundary, DeepSeek, model-selection, tracing, and App wiring tests:
  `161 passed, 2 skipped`.
- Final complete suite: `544 passed, 18 skipped`.
- Python `compileall` and `git diff --check`: passed.
- A local provider contract test serialized both actual strict ToolSpecs in one
  Beta-routed request without contacting DeepSeek.
- The separately authorized live DeepSeek request remains deliberately
  unexecuted in this implementation turn.
