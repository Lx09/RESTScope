# Operation Smoke Memory Workflow

Status: Completed and verified; intentionally uncommitted

## Objective

Replace independent Effect validation with a memory-driven Batch, Plan, Solve,
and Patch loop. Persist structured Failure knowledge for the lifetime of one
App while keeping raw response bodies and model transcripts out of the
database.

## Approved scope

- Remove `SmokeEffectAgent`, its model role, public contracts, traces, tests,
  and compatibility names.
- Let Planner classify current Failure Observations and query prior Failure
  memory through read-only tools.
- Let Solve query parameter memory and invoke Parameter Patch as a
  side-effect-free Agent tool.
- Apply only Solve-confirmed candidate references and run the next complete
  Batch after every item in the current Plan has finished.
- Persist Failures, Observations, Investigations, Parameters, and applied
  Patches; do not persist rejected Patch candidates.
- Replace the migration history with one current database baseline.
- Use one App-wide configurable seed for generated test values.

## Non-goals

- Cross-App memory recovery.
- Raw Batch, response body, HTTP transcript, or LLM transcript persistence.
- GitLab operations other than the explicitly authorized local
  `POST /projects`.
- Public GitLab, automatic project cleanup, or a reusable GitLab
  authentication subsystem.
- Compatibility aliases for removed Effect or request-level seed contracts.
- Git staging, commit, merge, worktree cleanup, or branch deletion.

## Decisions

- A passed Smoke result may mean the success threshold was reached, Planner
  found no debug work, or a complete Plan produced no applied Patch. The result
  records the exact stop reason and actual success rate.
- All items in a Plan run before the next Batch. Later Solve sessions see
  earlier applied Patches and may replace the same Parameter only while
  considering its prior Failure memory.
- Planner and Solve budget exhaustion is an operation error. One Patch Agent
  tool-call exhaustion is recoverable Solve feedback.
- Generator, Investigation, Parameter links, and Applied Patch writes are one
  atomic transaction.
- Old database files are incompatible with the new single baseline.

## Verification

Local and live verification is complete. The authorized live boundary is
the local `gitlab-test` container and only `POST /projects`; no other GitLab
operation is present in the live OpenAPI input.

### GitLab live findings

- The GitLab 18.9.2 OpenAPI assets do not contain project creation, so the live
  harness supplies a focused one-operation contract for the real endpoint.
- A session Cookie authenticates `GET /api/v4/user` but GitLab write APIs also
  require the authenticated page's CSRF token. Cookie-only Batch requests
  returned 401; Cookie plus `X-CSRF-Token` returned 201.
- The first trace/code audit exposed that public Batch request summaries
  copied trusted authentication headers. A regression test was observed red,
  then the summary boundary was changed to retain header names with
  `[redacted]` values while the transport still receives the originals.
- Final live command:
  `RUN_GITLAB_POST_PROJECTS_SMOKE_E2E=1 uv run --extra tracing pytest -q -s
  tests/test_gitlab_post_projects_smoke_live.py`
  → `1 passed in 4.29s`.
- Final run
  `gitlab-post-projects-smoke-20260729T045826Z-fccf0c6f` executed one Batch
  containing ten real project-creation cases. All ten returned 201, the Smoke
  success rate was 1.0, and it stopped with `success_rate_reached`.
- The diagnosis probe and two successful ten-case Batches created 21 private
  test projects in the disposable GitLab container. They were intentionally
  retained because deleting them would exercise a second operation outside the
  authorized live boundary.
- Phoenix project
  `restscope-gitlab-post-projects-smoke-20260729T045826Z-fccf0c6f` contains 35
  spans, all `OK`: App, Supervisor, one attempt, Coordinator, one Batch, ten
  Cases, ten Behavior Monitor observations, and ten Resource Identifier
  observations. No Planner, Solve, Patch, or LLM span was needed after the
  initial Batch passed.
- Report and trace scans found no root password, session Cookie value, or CSRF
  token value. The final artifacts are ignored local files under
  `artifacts/gitlab-post-projects-smoke/`.

### Local verification

- Focused workflow, persistence, App, tracing, and live-harness tests:
  `uv run pytest -q ...` → `105 passed, 2 skipped`.
- `uv run pytest -q` → `467 passed, 4 skipped, 2 failed`.
- `uv run --extra tracing pytest -q` →
  `467 passed, 4 skipped, 2 failed`.
- Both failures were reproduced before this implementation on unmodified local
  `main`: object-cardinality recovery and a test that dereferences
  `OperationExecutionReport.report`.
- `uv run python -m compileall -q restscope tests`: passed.
- `git diff --check`: passed.
- Boundary and residual scans confirm the old Agent category package, Effect
  package/role, old Coordinator names, candidate revision lifecycle, and
  compatibility aliases are absent. Explicit tests still spell two retired
  names only to assert that they cannot be restored.
- The work remains unstaged and uncommitted. Commit, merge, and worktree/branch
  cleanup still require separate authorization.

## 2026-07-30 Phoenix Evals tool-recognition follow-up

Status: Implemented and locally verified; live Eval rerun not authorized

### Evidence

- The persisted Phoenix volume contains two Solve experiments for the same
  dataset version and prompt hash.
- The failing run consumed 12 Solve outputs, repeated one HTTP probe, skipped
  Parameter memory and Parameter Patch, returned `no_patch`, and scored zero on
  all four applicable Solve behavior checks.
- The passing repeat still consumed 20 total outputs. Its trace shows repeated
  guesses of `path/projectId` and `projectId` before discovering the accepted
  semantic handle `path.projectId`.
- Runtime allowed exactly one tool call per output, but the system prompt did
  not state that rule. Parameter tool schemas accepted generic strings and did
  not enumerate the current operation's valid semantic handles.

### Localized fix

- Failure Solve now states the one-tool-per-output protocol and distinguishes
  dotted semantic handles from slash-separated internal node identities.
- Parameter-memory and Parameter-Patch tool schemas enumerate the exact
  semantic handles valid for the current operation.
- The prompt tells Solve to use HTTP only when current Batch and memory evidence
  cannot distinguish the root cause.
- No public DTO, database record, persistence boundary, dependency, or workflow
  package boundary changed.

### Verification

- Red/green feedback loop:
  `uv run pytest -q
  tests/test_failure_solver_agent.py::test_solve_tool_contract_exposes_the_shortest_valid_tool_path`
  changed from `solve_budget_exhausted` to `1 passed`.
- Focused Failure Solve, DeepSeek, and Plan/Solve tests:
  `uv run pytest -q tests/test_failure_solver_agent.py
  tests/test_llm_deepseek.py tests/test_operation_smoke_plan_solve.py`
  → `26 passed`.
- Full suite: `467 passed, 5 skipped, 3 failed`. Two failures are the recorded
  pre-existing baselines. The third is caused by the preserved, pre-existing
  untracked `tests/test_gitlab_smoke_e2e_live.py` containing retired package
  names that the boundary test intentionally scans.
- A new live DeepSeek/Phoenix Eval would contact an external model and may incur
  cost, so it remains unrun pending explicit authorization.
