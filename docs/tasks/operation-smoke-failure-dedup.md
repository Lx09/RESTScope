# Operation Smoke Failure Dedup

Status: Implemented and locally verified; uncommitted

## Objective

Replace the Operation Smoke Planner with a current-Batch Failure deduplication
flow. Batch Testing returns case results only. Deterministic code first removes
identical normalized error messages; when several distinct messages remain, an
LLM groups them by their complete suspected causal Parameter set. Every
resulting Failure carries exactly one representative test case into Solve.

## Approved scope

- Delete Batch Failure Report types and behavior.
- Replace `SmokePlanAgent` with `FailureDedupAgent` plus deterministic
  `FailureDeduplicator`.
- Keep one test case per Fingerprint and one test case per final Failure.
- Do not expose item IDs or Fingerprint references to the model.
- Validate model coverage and Parameter handles, then use Markdown correction
  feedback until the output is valid or its budget is exhausted.
- Remove Planner history retrieval, no-debug decisions, and related
  compatibility names.
- Render all five direct LLM decisions as Markdown; render bounded HTTP
  request/response evidence as JSON inside Markdown.
- Preserve Solve Parameter history and applied-Patch memory.

## Non-goals

- No target API, DeepSeek, Phoenix, or other live external call.
- No LLM judge.
- No change to Patch application semantics or the Batch success threshold.
- No compatibility aliases for deleted Planner or Failure Report names.

## Material decisions

- The exact Fingerprint is the normalized error-message text.
- Exact Fingerprint deduplication happens before semantic Parameter grouping.
- First-seen Batch order selects every representative test case.
- A single Fingerprint bypasses the LLM and carries unknown
  `suspected_parameters`; an empty list is reserved for an Agent-classified
  operation-level Failure.
- A valid Dedup response is recorded only after deterministic validation.
- Every retry is a complete replacement response and consumes the shared Dedup
  output budget.

## Verification

Offline verification completed on 2026-07-31:

- Focused Dedup, Context, Solve, Coordinator, Memory, tracing-contract, package
  boundary, and evaluation tests: `84 passed, 2 skipped`.
- Focused Failure Fingerprint and Batch boundary regression tests after adding
  field-keyed GitLab error extraction: `29 passed`.
- Full ordinary dependency suite excluding one independently reproduced
  pre-existing Generator failure: `496 passed, 4 skipped, 1 deselected`.
- Full tracing dependency suite with the same exclusion:
  `496 passed, 4 skipped, 1 deselected`.
- Phoenix Evals deterministic Dataset, task Adapter, metadata, isolation, and
  code-evaluator tests: `13 passed`.
- `python -m compileall -q restscope tests evaluations`: passed.
- `git diff --check`: passed.
- Residual searches found no retired Planner, Failure Report, Memory-candidate,
  budget, status, or package names in production code, current tests, or
  current user documentation. Historical specs, plans, and diagrams retain
  their original text and are superseded by this record.

After explicit authorization, the single GitLab live acceptance test ran
against `POST /projects` and exported traces to local Phoenix:

- `tests/test_gitlab_post_projects_smoke_live.py`: `1 passed in 5.75s`.
- One complete Batch sent 10 real requests; all 10 returned `201`, producing a
  `1.0` success rate and `success_rate_reached`.
- The successful first Batch correctly used no LLM, Dedup, Solve, or Patch
  spans. This live acceptance therefore verifies the success fast path, not the
  Failure Dedup Agent path; deterministic tests cover that path.
- Artifact directory:
  `artifacts/gitlab-post-projects-smoke/gitlab-post-projects-smoke-20260731T040111Z-3406d62c`.
- Phoenix project:
  `restscope-gitlab-post-projects-smoke-20260731T040111Z-3406d62c`.

The excluded test is
`test_object_cardinality_requires_a_generator_set_that_always_conforms`.
It fails identically on the local `main` worktree at the feature branch's base:
an accepted optional-property inclusion change still leaves the operation
disabled. This Generator catalog defect is outside the approved Failure Dedup
scope and was not changed here.
