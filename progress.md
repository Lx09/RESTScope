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
| 2026-07-30 | Docker daemon access denied by sandbox | 1 | Used approved read-only Docker inspection and found the persisted Evals volume. |
| 2026-07-30 | `uv` cache was outside the sandbox | 1 | Reran the focused test with approved `uv run pytest` access. |
| 2026-08-01 | Combined secret-scan shell quoting was invalid | 1 | Split file inspection and sensitive-value scans into simpler commands. |
| 2026-08-01 | Local parser check could not write the configured desktop log | 1 | Redirected RESTScope logging to a writable temporary path for verification. |

## 5-Question Reboot Check

| Question | Answer |
|---|---|
| Where am I? | Completed and verified, intentionally uncommitted |
| Where am I going? | Awaiting separate commit authorization |
| What's the goal? | Replace Effect with the approved memory-driven workflow |
| What have I learned? | See `findings.md` |
| What have I done? | Implemented, locally verified, live-tested, and trace-audited the approved workflow |

## Session: 2026-07-30

### Phase 8: Agent tool-recognition diagnosis

- **Status:** completed
- Actions taken:
  - Loaded the repository rules plus the diagnosing-bugs and
    planning-with-files skills.
  - Confirmed the pre-existing untracked GitLab live test will remain
    untouched.
  - Started constructing a local red-capable feedback loop before forming a
    code-level theory.
  - Located the two tool-using Agents and historical Phoenix trace exports that
    can serve as local evidence.
  - Determined that checked-in exports cover the retired diagnosis workflow,
    not the current Failure Solve implementation.
  - Recovered the current Phoenix experiments from the older persisted volume.
  - Confirmed a deterministic evaluation failure: Solve skipped required
    Parameter-memory and Patch tools, duplicated an HTTP probe, and returned
    `no_patch`. A repeated run passed but still consumed 20 outputs.
  - Isolated two prompt/tool-contract mismatches: valid dotted semantic handles
    are absent from the prompt and tool schemas, and runtime's one-tool-call
    rule is absent from the system prompt.
  - Added and ran the public Failure Solve feedback loop red:
    `solve_budget_exhausted` instead of `applied_patch`.
  - Applied the smallest contract fix: dynamic semantic-handle enums, explicit
    one-tool-per-output guidance, and bounded HTTP-probe guidance.
  - Reran the exact feedback loop green (`1 passed`) and the focused
    Failure Solve/DeepSeek/Plan-Solve suite green (`26 passed`).
  - Ran the full suite: `467 passed, 5 skipped, 3 failed`; two failures are
    recorded baselines and one comes from the preserved untracked GitLab E2E
    test being scanned by the workflow-boundary test.
  - Confirmed the localized fix is already preserved in commit `885cbc9` and
    the current task record documents its focused verification.
