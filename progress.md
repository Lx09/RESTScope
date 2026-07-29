# Progress Log

## Session: 2026-07-29

### Phase 1: Contracts, glossary, and global seed

- **Status:** completed
- Actions taken:
  - Loaded project rules and the planning, deep-Module, domain-modeling, and
    TDD skills.
  - Confirmed the existing workflow refactor is isolated in the approved
    feature worktree.
  - Recorded the approved architecture and pre-agreed test seams.
  - Completed the first red/green slice for App-wide randomness and removed
    request-level seed/effect-budget fields.
  - Added the resolved seed to run reports and Coordinator traces.
- Files created:
  - `task_plan.md`
  - `findings.md`
  - `progress.md`

### Phases 2–4: Memory, Planner, and Solve/Patch

- **Status:** completed
- Actions taken:
  - Added the normalized Smoke Memory domain, SQLAlchemy Adapter, and shared
    atomic Unit of Work.
  - Added Planner Failure aliases, read-only lookup, full observation coverage,
    and deterministic validated writes.
  - Moved Patch Agent behind Solve's tool loop, including Parameter history,
    session candidate references, nested budgets, and atomic Patch application.

### Phase 5: Coordinator and Effect removal

- **Status:** completed
- Actions taken:
  - Enforced full-Plan processing before the next complete Batch.
  - Added the three passed stop reasons and errored budget outcomes.
  - Deleted Effect Agent and removed candidate/rollback revision state.

### Phases 6–7: Documentation, GitLab live acceptance, and final verification

- **Status:** completed
- Actions taken:
  - Updated current rules, README, reading guide, database design, glossary,
    tests guide, and task records.
  - Added rich responsibility, boundary, and state-transition comments across
    the new Operation Smoke, Memory, and live-test paths.
  - Added one opt-in GitLab `POST /projects` live acceptance test.
  - Diagnosed Cookie-only 401 responses; GitLab writes also require the
    authenticated page's CSRF token.
  - Observed a failing credential-redaction regression, fixed the public
    request-summary boundary, and reran it green.
  - Reached ten 201 responses in one complete Batch and audited all 35 Phoenix
    spans.

## Test Results

| Test | Expected | Actual | Status |
|---|---|---|---|
| Pending first red slice | New public seed/stop contracts fail | Not run | Pending |
| Randomness/public seed slice | New contracts fail before implementation | 6 expected failures | Red |
| Randomness/public seed slice | Focused contracts pass after implementation | 11 passed | Green |
| Memory/Planner/Solve/Patch/Coordinator slices | New workflow contracts pass | 28 passed | Green |
| Final focused suite | Workflow, persistence, tracing, and live harness | 105 passed, 2 skipped | Green |
| GitLab live acceptance | One Batch reaches at least 80% | 10/10 returned 201; 1 passed | Green |
| Full suite | No new failures beyond baseline | 467 passed, 4 skipped, 2 known baseline failures | Expected baseline |
| Full tracing suite | Same behavior with tracing extra | 467 passed, 4 skipped, 2 known baseline failures | Expected baseline |
| Compile and diff checks | No syntax or whitespace errors | Passed | Green |

## Error Log

| Timestamp | Error | Attempt | Resolution |
|---|---|---:|---|
| 2026-07-29 | Repository-local planning skill path missing | 1 | Used installed skill path. |
| 2026-07-29 | App/Coordinator patch context mismatch | 1 | Read exact files and split the patch into precise hunks. |
| 2026-07-29 | Focused pytest node was not found | 1 | Searched the test module for valid public test names. |
| 2026-07-29 | Combined memory export patch did not match one context | 1 | Inspected the exact files and split the change into narrow patches. |
| 2026-07-29 | Eager workflow facades caused a database import cycle | 1 | Switched only approved facade names to lazy resolution. |
| 2026-07-29 | GitLab Cookie-only writes returned 401 | 1 | Added authenticated CSRF header; direct POST returned 201. |
| 2026-07-29 | Trusted auth headers appeared in Batch reports/spans | 1 | Added a red regression and redacted them before the public summary boundary. |
| 2026-07-29 | First successful live run used an old span assertion | 1 | Corrected the live harness and reran it green. |

## 5-Question Reboot Check

| Question | Answer |
|---|---|
| Where am I? | Completed and verified, intentionally uncommitted |
| Where am I going? | Awaiting separate commit authorization |
| What's the goal? | Replace Effect with the approved memory-driven workflow |
| What have I learned? | See `findings.md` |
| What have I done? | Implemented, locally verified, live-tested, and trace-audited the approved workflow |
