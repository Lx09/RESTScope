# Progress Log

## Session: 2026-08-05

### Phase 1: Discovery and executable seams
- **Status:** complete
- **Started:** 2026-08-05
- Actions taken:
  - Read project governance, code-reading guide, current Dedup/Solve implementation, tests, and task history during planning.
  - Verified focused pre-change baseline: 52 passed.
  - Created branch `codex/merge-failure-agents` and its dedicated worktree.
  - Captured the approved reference-only worklist design.
  - Inventoried all old package roles, imports, tests, evaluations, and public budget/result fields.
  - Confirmed the persistence change requires a round-level Unit of Work because current Failure and Attempt writes are separate.
  - Selected `TestCaseCatalog.valid_parameters` and issued `TC*` values as the trusted source for worklist validation.
  - Selected the existing `AgentToolbox` validation/failure path for atomic worklist tool calls.
  - Implemented and verified the first reference-only worklist contracts: 6 tests pass.
  - Added a shared Operation-level model-output guard and verified it with the worklist contracts: 10 tests pass.

### Phase 2: Resolution contracts and worklist
- **Status:** complete
- **Started:** 2026-08-05
- Actions taken:
  - Implemented strict reference-only worklist schemas and atomic optimistic-revision replacement.
  - Added bounded read/write worklist tools with mechanical E/TC/P/parameter validation.
  - Confirmed candidate details need a separate read-only session registry before the continuous Agent loop is added.
  - Added the candidate registry and bounded `parameter_patch.read_candidate` projection; focused contracts now pass 12 tests.
  - Implemented the first continuous Resolution session: deterministic exact-message folding, minimal initial prompt, reference tools, finish retry, per-item round prompts, and shared hard-stop behavior.
  - Deepened Patch persistence into a reusable side-effect-free state transition.
  - Added registry-only finalization with stable Failure de-duplication, candidate overlap checks, fresh combined validation, per-candidate events, and one transaction; all four database scenarios pass.
- Files created/modified:
  - `task_plan.md`
  - `findings.md`
  - `progress.md`

### Phase 3: Continuous Agent and finalization
- **Status:** complete
- Actions taken:
  - Connected OpenAPI, Test Case, worklist, candidate, Parameter Memory, Parameter Patch, Review, and current-operation Probe tools to one continuous session.
  - Replaced per-Agent budgets and repetition stops with one shared Operation-level 1000-output guard.
  - Added registry-only finalization and one atomic Unit of Work for decided Failures, Attempts, compatible candidates, and change events.
  - Verified the Parameter Patch/Review loop independently: 61 tests pass.

### Phase 4: Workflow replacement
- **Status:** complete
- Actions taken:
  - Replaced Operation Smoke composition, request/results, model role, Supervisor failure kind, tracing names, and package-boundary expectations with Resolution naming.
  - Deleted the old `failure_dedup` and `failure_solver` packages, factories, exports, and direct tests.
  - Replaced three Phoenix suites with one `resolution` suite covering semantic merge, semantic split, and a real nested Patch/Review candidate flow without database or target HTTP access.
  - Full pre-Evaluation-migration suite passed: 623 passed, 18 skipped.

### Phase 5: Verification and delivery
- **Status:** complete
- Actions taken:
  - Enabled the optional Evaluation dependency group and verified the single Resolution suite: 7 passed.
  - Fixed the unified Agent role attribute exposed only when tracing dependencies are active.
  - Re-ran the complete optional-dependency suite: 653 passed, 5 skipped.
  - Verified the local Evaluation registry lists only three `resolution-*` scenarios and no retired suite.
  - Removed the last dead repeated-proposal fingerprint helper during final diff review.
  - Final fresh verification: 653 passed, 5 skipped; compileall, 8 package-boundary tests, Evaluation registry listing, and `git diff --check` passed.
  - Confirmed the feature worktree has no staged or committed task changes.

### Phase 6: Bounded GitLab live diagnosis
- **Status:** complete
- Actions taken:
  - Ran the five-operation local GitLab live scenario under a caller-enforced
    600-second hard cutoff with real configured DeepSeek calls.
  - Reproduced and fixed mixed strict/non-strict DeepSeek tool projection and
    an invalid 1,200-character limit on exact session Failure messages.
  - Observed that pre-worklist HTTP Probe calls bypassed active-item round
    feedback; added the missing mechanical safety requirement while preserving
    unrestricted repeated probes after an item is active.
  - Reproduced the provider returning thinking tool calls without required
    `reasoning_content`; expanded the safe pre-tool compatibility retry from
    two total requests to three and retained immediate rejection of malformed
    local history.
  - Final live run reached the 600-second cutoff without a traceback. It
    completed two operations and atomically wrote five applied Patch Attempts
    and five Generator change events before beginning the third operation.
  - Phoenix recorded 382 flushed spans, including 93 LLM calls and 40 real Test
    Case executions. Its sole ERROR span was an expected rejected lookup of an
    unissued `P2`, after which the Agent continued.

### Phase 7: Failure investigation tool refinement
- **Status:** complete
- Actions taken:
  - Removed `test_case.get_failure_messages` from Resolution's registered tool
    set while retaining its Catalog query, tool spec, constant, and export.
  - Documented the on-demand investigation path from
    `openapi.list_response_fields` to `test_case.get_response_field_value`.
  - Added public-seam tests for the four-tool registration, initial prompt and
    model tool list, and one continuous field-discovery/value-query session.
  - Focused Catalog/Resolution checks passed: 18 tests.
  - Evaluation, package-boundary, and tracing checks passed: 24 tests.
  - Full optional-dependency suite passed: 657 tests, 5 skipped.
  - Python compilation and `git diff --check` passed after the final edit.

## Test Results
| Test | Input | Expected | Actual | Status |
|------|-------|----------|--------|--------|
| Focused pre-change baseline | Dedup, Solve, Coordinator, package boundaries | Existing behavior passes | 52 passed | pass |
| Worklist and output limit | New focused tests | Reference validation and shared limit pass | 10 passed | pass |
| Candidate registry and read tool | New focused tests | Precise candidate remains private; summary is recoverable | 12 passed | pass |
| Continuous Resolution session | New Agent/worklist/limit tests | Same-session retries and progressive prompts pass | 17 passed | pass |
| Atomic Resolution finalization | New database tests | Multi-candidate validation and rollback pass | 4 passed | pass |
| Parameter Patch and Review | Updated focused tests | Repeats allowed; only shared hard guard stops | 61 passed | pass |
| Full suite before Evaluation migration | Entire default test group | No regressions in installed test group | 623 passed, 18 skipped | pass |
| Core suite after Probe association | Resolution, Patch/Review, Coordinator | Reference and repeat behavior remains valid | 91 passed | pass |
| Phoenix Evaluation group | Single Resolution suite | Scenarios, task, evaluators, CLI role pass offline | 7 passed | pass |
| Full suite with optional dependencies | Entire repository | Optional tracing and Evaluation paths pass | 653 passed, 5 skipped | pass |
| Final focused architecture group | Patch, finalizer, Evaluation, boundaries | Current seams pass after self-review | 80 passed | pass |
| Live-found focused regressions | Resolution Agent and DeepSeek provider | Probe ownership and bounded missing-reasoning recovery pass | 43 passed | pass |
| Full suite after live-found fixes | Entire repository | No offline regressions | 656 passed, 5 skipped | pass |
| Failure investigation refinement | Catalog, Resolution, Evaluation, boundaries, tracing | Four-tool interface and progressive response evidence path work | 42 passed | pass |
| Full suite after tool refinement | Entire repository | No offline regressions | 657 passed, 5 skipped | pass |
| Final bounded GitLab live run | Five operations, 600-second cutoff | No crash; capture real progress | 2 operations and 5 Patch Attempts completed before cutoff | partial |

## Error Log
| Timestamp | Error | Attempt | Resolution |
|-----------|-------|---------|------------|
| 2026-08-05 | 6 worklist tests failed with missing new package | 1 | Expected red phase before implementing contracts |
| 2026-08-05 | Candidate-registry patch did not match the current test tail | 1 | No partial edit occurred; re-apply with current anchors |
| 2026-08-05 | Inspection used the old flat `test_case_catalog.py` path | 1 | Located the current workflow subpackage and continued with its public files |
| 2026-08-05 | 5 continuous Resolution tests failed with missing Agent export | 1 | Expected red phase before adding the continuous session |
| 2026-08-05 | First Agent run omitted a CompactTextWriter section and read the wrong ToolFailure attribute | 1 | Add a tool-result section and use the safe_message contract |
| 2026-08-05 | Public-schema migration patch used an inexact docstring anchor | 1 | No partial edit occurred; replace the small schema module as one exact patch |
| 2026-08-05 | 8 coordinator/boundary tests still asserted removed Dedup/Solve seams | 1 | Expected migration failures; replace them with Resolution ordering and public-contract scenarios |
| 2026-08-05 | Verification shell has no `python` command | 1 | Use the available `python3` executable |
| 2026-08-05 | Planning-file update used an ambiguous status anchor | 1 | Re-applied with phase-specific anchors; no partial edit occurred |
| 2026-08-05 | Focused test command used a non-activated `pytest` executable | 1 | Run verification through the worktree's `.venv/bin/pytest` |
| 2026-08-05 | Test-fixture rename patch expected one extra import occurrence | 1 | Re-applied with the exact seven imports; no partial edit occurred |
| 2026-08-05 | Optional tracing dependencies activated one missing Resolution role span attribute | 1 | Project the bounded internal request role to `restscope.llm.role` and rerun the complete optional suite |
| 2026-08-05 | DeepSeek rejected a mixed strict/non-strict Resolution tool set | 1 | Project all tools non-strict only for the model when HTTP Probe is present; retain local schema validation |
| 2026-08-05 | Exact target Failure text exceeded the E-registry schema cap | 1 | Remove the registry cap and keep prompt bounding in the Context adapter |
| 2026-08-05 | Resolution repeatedly probed before naming an active item | 1 | Require `active_item_id` before HTTP Probe and add a regression scenario |
| 2026-08-05 | DeepSeek thinking tool calls omitted `reasoning_content` on two consecutive responses | 1 | Add a third bounded pre-tool attempt; reject after exhaustion and never fabricate continuation content |

## 5-Question Reboot Check
| Question | Answer |
|----------|--------|
| Where am I? | Phase 4: workflow replacement and current-contract cleanup |
| Where am I going? | Finish Evaluation/docs migration, then focused and full verification |
| What's the goal? | One reference-based Failure Resolution Agent with a minimal harness |
| What have I learned? | See `findings.md` |
| What have I done? | Implemented the continuous Agent, atomic finalizer, workflow replacement, and single Resolution Eval suite |
